from prometheus_client import Counter, Histogram, Gauge

api_requests_total = Counter(
    "api_requests_total",
    "Total Instagram Graph API requests",
    ["endpoint", "status"],
)
api_errors_total = Counter(
    "api_errors_total",
    "Total Instagram Graph API errors",
    ["endpoint", "error"],
)
leads_collected_total = Counter(
    "leads_collected_total",
    "Total leads collected from post engagement",
)
posts_processed_total = Counter(
    "posts_processed_total",
    "Total posts synced from own account",
)
job_duration_seconds = Histogram(
    "job_duration_seconds",
    "Background job execution duration",
    ["job_name"],
)
active_leads_gauge = Gauge(
    "active_leads_total",
    "Current total number of leads in database",
)
queue_depth_gauge = Gauge(
    "celery_queue_depth",
    "Number of pending tasks in Celery queue",
    ["queue"],
)
