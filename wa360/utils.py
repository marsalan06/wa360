"""
Utility functions for WhatsApp 360dialog integration
"""
import re
import logging

logger = logging.getLogger(__name__)

def normalize_msisdn(msisdn: str) -> str:
    """Normalize MSISDN to standard format with + prefix"""
    digits = re.sub(r"[^\d+]", "", msisdn or "")
    if digits and not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    return digits

def digits_only(msisdn: str) -> str:
    """Return international number with digits only (no '+'). Sandbox expects this."""
    return re.sub(r"\D", "", msisdn or "")

# ============================================================================
# OPENAI CLIENT MANAGER
# ============================================================================

class OpenAIManager:
    """Manages OpenAI client instances and API calls"""
    
    def __init__(self, api_key, model="gpt-4o-mini", temperature=0.7, max_tokens=1000):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of OpenAI client"""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key)
            except ImportError:
                raise Exception("OpenAI package not installed. Run: pip install openai")
            except Exception as e:
                raise Exception(f"Failed to initialize OpenAI client: {str(e)}")
        return self._client
    
    def chat_completion(self, system_prompt, user_message, temperature=None, max_tokens=None):
        """Make a chat completion request"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            raise Exception(f"AI request failed: {str(e)}")
    
    @classmethod
    def from_llm_config(cls, llm_config):
        """Create OpenAI manager from LLMConfiguration"""
        api_key = llm_config.get_api_key()
        if not api_key:
            raise Exception("No valid API key found in LLM configuration")
        
        return cls(
            api_key=api_key,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens
        )

# ============================================================================
# CONVERSATION SUMMARIZATION
# ============================================================================

def get_summarization_prompt():
    """Generate prompt for conversation summarization"""
    return """You are a professional conversation summarizer for business communications.

    Analyze the WhatsApp conversation between a sales engineer and client, then create a concise summary.

    SUMMARIZATION GUIDELINES:
    - Focus on key business points, decisions, and action items
    - Include meeting schedules, project updates, and important dates
    - Note client preferences, requirements, and concerns
    - Highlight any commitments made by either party
    - Keep the summary professional and factual
    - Structure the summary clearly with key points

    SUMMARY STRUCTURE:
    1. **Conversation Overview**: Brief context of the discussion
    2. **Key Points Discussed**: Main topics and decisions
    3. **Action Items**: Tasks, commitments, and next steps
    4. **Meeting Information**: Any scheduled meetings or follow-ups
    5. **Client Notes**: Important client preferences or requirements

    Provide a clear, concise summary that helps maintain context for future conversations."""

def build_conversation_text(conversation):
    """Build formatted conversation text from messages"""
    messages = conversation.messages.order_by('created_at')
    if not messages.exists():
        return "No messages to summarize"
    
    conversation_text = f"Conversation with {conversation.wa_id}:\n\n"
    for msg in messages:
        direction = "Client" if msg.direction == 'in' else "Sales Engineer"
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        conversation_text += f"[{timestamp}] {direction}: {msg.text}\n"
    
    return conversation_text

def summarize_conversation(llm_config, conversation):
    """Generate AI summary for a conversation"""
    try:
        # Create OpenAI manager
        openai_manager = OpenAIManager.from_llm_config(llm_config)
        
        # Build conversation text
        conversation_text = build_conversation_text(conversation)
        if conversation_text == "No messages to summarize":
            return conversation_text
        
        # Generate summary
        summary_content = openai_manager.chat_completion(
            system_prompt=get_summarization_prompt(),
            user_message=f"Please summarize this conversation:\n\n{conversation_text}",
            temperature=0.3,  # Lower temperature for consistent summaries
            max_tokens=min(llm_config.max_tokens, 800)  # Limit summary length
        )
        
        # Create or update summary record
        from .models import ConversationSummary
        summary, created = ConversationSummary.objects.get_or_create(
            conversation=conversation,
            defaults={
                'content': summary_content,
                'message_count': conversation.messages.count()
            }
        )
        
        if not created:
            summary.content = summary_content
            summary.message_count = conversation.messages.count()
            summary.save()
        
        return summary_content
        
    except Exception as e:
        logger.error(f"Failed to summarize conversation {conversation.id}: {str(e)}")
        raise Exception(f"Summarization failed: {str(e)}")
