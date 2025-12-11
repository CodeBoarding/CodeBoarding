from monitoring import MonitoringCallback, RunStats


class MonitoringMixin:
    def __init__(self):
        # 1. Isolated stats for this specific agent instance
        self.agent_stats = RunStats()
        self.agent_monitoring_callback = MonitoringCallback(stats_container=self.agent_stats)

        # 2. Connection to the global stats (for CLI reporting/aggregation)
        # Passing no args to MonitoringCallback makes it use the current context stats (if available)
        self.global_monitoring_callback = MonitoringCallback()

    def get_monitoring_results(self) -> dict:
        """Return monitoring statistics."""
        return self.agent_stats.to_dict()
