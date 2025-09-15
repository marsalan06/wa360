# Celery Beat Periodic Tasks Configuration
# This file contains all scheduled tasks for Celery Beat

CELERY_BEAT_SCHEDULE = {
    'check-periodic-schedules': {
        'task': 'wa360.tasks.check_and_send_periodic_messages',
        'schedule': 60.0,  # Check every minute
    },
}
