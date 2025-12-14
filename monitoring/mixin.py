from monitoring.callbacks import MonitoringCallback
from monitoring.stats import RunStats


class MonitoringMixin:
    def __init__(self):
        # 1. Isolated stats for this specific agent instance
        self.agent_stats = RunStats()
        self.agent_monitoring_callback = MonitoringCallback(stats_container=self.agent_stats, log_results=False)

        # 2. Connection to the global stats (for CLI reporting/aggregation)
        self.global_monitoring_callback = MonitoringCallback()

    def get_monitoring_results(self) -> dict:
        """Return monitoring statistics."""
        return self.agent_stats.to_dict()
