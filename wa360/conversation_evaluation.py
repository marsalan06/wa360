"""
Conversation Status Evaluation using Pydantic AI
Analyzes conversation summaries to determine client engagement and next actions
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent
import logging

logger = logging.getLogger(__name__)


class ConversationStatus(str, Enum):
    """Possible conversation statuses based on client engagement"""
    CONTINUE = "continue"  # Client is actively engaged, don't send periodic msg
    SCHEDULE_LATER = "schedule_later"  # Client postponed, send periodic msg later
    CLOSE = "close"  # Client disinterested, don't send periodic msg


class ConversationEvaluation(BaseModel):
    """Structured evaluation result for conversation status"""
    status: ConversationStatus = Field(
        description="The recommended action based on client engagement"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence level of the evaluation (0.0 to 1.0)"
    )
    reasoning: str = Field(
        description="Detailed explanation of why this status was chosen"
    )
    client_sentiment: str = Field(
        description="Overall sentiment of client's last responses (positive/neutral/negative)"
    )
    engagement_level: str = Field(
        description="Level of client engagement (high/medium/low)"
    )
    suggested_timing: Optional[str] = Field(
        default=None,
        description="Suggested timing for next contact (e.g., 'next week', 'next month')"
    )


class ConversationEvaluator:
    """AI-powered conversation evaluator using Pydantic AI"""
    
    def __init__(self, api_key: str, model_name: str = "gpt-4o"):
        """Initialize the evaluator with OpenAI API key"""
        try:
            # Use the simplified Pydantic AI 1.0.10 syntax
            self.agent = Agent(
                f'openai:{model_name}',
                output_type=ConversationEvaluation,
                instructions='You are an expert conversation analyst specializing in client engagement evaluation for sales and business development.'
            )
            logger.info(f"ConversationEvaluator initialized with model: {model_name}")
        except Exception as e:
            logger.error(f"Failed to initialize ConversationEvaluator: {str(e)}")
            raise
    
    def evaluate_conversation(self, conversation_summary: str, conversation_context: str = "") -> ConversationEvaluation:
        """
        Evaluate conversation status based on summary and context
        
        Args:
            conversation_summary: AI-generated summary of the conversation
            conversation_context: Additional context about the conversation
            
        Returns:
            ConversationEvaluation: Structured evaluation result
        """
        try:
            evaluation_prompt = self._build_evaluation_prompt(conversation_summary, conversation_context)
            
            result = self.agent.run_sync(evaluation_prompt)
            
            logger.info(f"Conversation evaluation completed: {result.output.status}")
            return result.output
            
        except Exception as e:
            logger.error(f"Failed to evaluate conversation: {str(e)}")
            # Return safe default evaluation
            return ConversationEvaluation(
                status=ConversationStatus.CONTINUE,
                confidence=0.5,
                reasoning=f"Evaluation failed: {str(e)}. Defaulting to continue.",
                client_sentiment="unknown",
                engagement_level="unknown"
            )
    
    def _build_evaluation_prompt(self, conversation_summary: str, conversation_context: str) -> str:
        """Build the evaluation prompt for the AI agent"""
        
        prompt = f"""
You are an expert conversation analyst specializing in client engagement evaluation for sales and business development.

TASK: Analyze the conversation summary and determine the client's engagement level and recommended next action.

CONVERSATION SUMMARY:
{conversation_summary}

ADDITIONAL CONTEXT:
{conversation_context}

EVALUATION CRITERIA:

1. **CONTINUE** - Choose this when:
   - Client is actively engaged and responding positively
   - Client is asking questions or showing interest
   - Client is participating in ongoing discussion
   - Client has not indicated any postponement or disinterest
   - Recent messages show active participation

2. **SCHEDULE_LATER** - Choose this when:
   - Client explicitly asks to be contacted later (e.g., "contact me next month")
   - Client indicates they're busy but not disinterested
   - Client postpones but shows potential future interest
   - Client says "I'll think about it" or similar postponement phrases
   - Client requests follow-up at a specific future time

3. **CLOSE** - Choose this when:
   - Client explicitly says "no", "not interested", "don't contact me"
   - Client shows clear disinterest or rejection
   - Client has stopped responding for an extended period
   - Client indicates they don't need the service/product
   - Conversation has reached a natural conclusion with no follow-up needed

ANALYSIS REQUIREMENTS:
- Analyze the client's sentiment in their last responses
- Assess their engagement level (high/medium/low)
- Determine their intent and preferences
- Provide confidence level for your assessment
- Suggest appropriate timing if scheduling later

Be conservative in your evaluation - err on the side of continuing conversations unless there are clear signals to close or postpone.
"""
        
        return prompt


def create_evaluator_from_llm_config(llm_config) -> ConversationEvaluator:
    """Create ConversationEvaluator from LLMConfiguration object"""
    try:
        api_key = llm_config.get_api_key()
        if not api_key:
            raise Exception("No API key found in LLM configuration")
        
        # Set the OpenAI API key as environment variable for Pydantic AI
        import os
        os.environ['OPENAI_API_KEY'] = api_key
        
        return ConversationEvaluator(api_key=api_key, model_name=llm_config.model)
        
    except Exception as e:
        logger.error(f"Failed to create evaluator from LLM config: {str(e)}")
        raise
