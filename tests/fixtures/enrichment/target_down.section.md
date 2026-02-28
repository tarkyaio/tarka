## Enrichment

**Summary:** suspected_job_wide_scrape_failure

### Why

- Scrape target: job=api-metrics
- up==0 count (job): 3/5

### On-call next

- `sum(up{job="api-metrics"} == 0)`
- `count(up{job="api-metrics"})`
- Check Prometheus /targets for the affected job/instance and inspect the last scrape error (DNS/TLS/timeout/exporter crash).
