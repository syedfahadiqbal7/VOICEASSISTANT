import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/websocket_service.dart';

bool canCancelScheduledJob(Map<String, dynamic> job) {
  final rawStatus = job["status"]?.toString();
  final status = rawStatus == null || rawStatus.isEmpty
      ? ((job["enabled"] == false) ? "completed" : "pending")
      : rawStatus.toLowerCase();
  // A deferred execution may still be running in an approval callback. It is
  // pending only for scheduler polling and must not be presented as cancellable.
  return status == "pending" && job["execution_waiting"] != true;
}

String scheduledRunAtLabel(Map<String, dynamic> job) {
  final runAt = job["run_at"]?.toString() ?? "";
  // Preserve the complete ISO-8601 timestamp, including any explicit offset.
  return runAt.isEmpty ? "unspecified" : runAt;
}

String scheduledHistoryTitle(Map<String, dynamic> record) {
  final status = (record["status"] ?? "unknown").toString().toUpperCase();
  final label = (record["label"] ?? "Scheduled task").toString();
  return "$status · $label";
}

String scheduledHistorySubtitle(Map<String, dynamic> record) {
  final attempts = record["attempts"] ?? 0;
  final started = (record["started_at"] ?? "time unavailable").toString();
  return "Attempts: $attempts · $started";
}

String scheduledRecurrenceLabel(Map<String, dynamic> job) {
  final repeat = job["repeat"];
  if (repeat is Map && repeat["every_minutes"] != null) {
    return "every ${repeat["every_minutes"]} min";
  }
  if (repeat is Map && repeat["type"] == "calendar") {
    final frequency = repeat["frequency"]?.toString() ?? "calendar";
    final localTime = repeat["local_time"]?.toString() ?? "unspecified time";
    final timezone = repeat["timezone"]?.toString() ?? "unspecified timezone";
    var cadence = frequency;
    if (frequency == "weekly" && repeat["weekdays"] is List) {
      const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      final days = (repeat["weekdays"] as List)
          .whereType<int>()
          .where((day) => day >= 0 && day < dayNames.length)
          .map((day) => dayNames[day])
          .join(", ");
      cadence = "weekly ($days)";
    } else if (frequency == "monthly") {
      cadence = "monthly on day ${repeat["day_of_month"] ?? "?"}";
    }
    return "$cadence at $localTime ($timezone)";
  }
  return "";
}

class SchedulesScreen extends StatefulWidget {
  const SchedulesScreen({super.key});

  @override
  State<SchedulesScreen> createState() => _SchedulesScreenState();
}

class _SchedulesScreenState extends State<SchedulesScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ws = Provider.of<WebSocketService>(context, listen: false);
      if (ws.isConnected) {
        ws.requestSchedules();
        ws.requestSessionState();
        ws.requestScheduleHistory();
      }
    });
  }

  String _fmtWhen(Map<String, dynamic> job) {
    final rawStatus = job["status"]?.toString();
    final enabled = job["enabled"] != false;
    final waiting = job["execution_waiting"] == true;
    final status = waiting
        ? "WAITING"
        : (rawStatus == null || rawStatus.isEmpty
            ? (enabled ? "PENDING" : "DONE")
            : rawStatus.toUpperCase());
    final when = scheduledRunAtLabel(job);
    final recurrence = scheduledRecurrenceLabel(job);
    if (recurrence.isNotEmpty) {
      return "$status · $recurrence · next $when";
    }
    return "$status · at $when";
  }

  @override
  Widget build(BuildContext context) {
    final ws = Provider.of<WebSocketService>(context);

    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF1E293B),
        title: const Text("Schedules", style: TextStyle(fontWeight: FontWeight.bold)),
        actions: [
          IconButton(
            tooltip: "Refresh",
            onPressed: ws.isConnected
                ? () {
                    ws.requestSchedules();
                    ws.requestSessionState();
                    ws.requestScheduleHistory();
                  }
                : null,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            color: const Color(0xFF1E293B),
            child: Text(
              ws.isConnected
                  ? "Jobs run only while the desktop app is open. (${ws.jobsCount} job${ws.jobsCount == 1 ? '' : 's'})"
                  : "Connect to desktop first.",
              style: TextStyle(color: Colors.white.withOpacity(0.75), fontSize: 13),
            ),
          ),
          Expanded(
            flex: 3,
            child: !ws.isConnected
                ? const Center(
                    child: Text("Offline", style: TextStyle(color: Colors.white54)),
                  )
                : ws.jobs.isEmpty
                    ? Center(
                        child: Text(
                          "No scheduled jobs.\nUse desktop Schedules or chat:\nschedule in 2 minutes: Open notepad",
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.white.withOpacity(0.5), height: 1.4),
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: ws.jobs.length,
                        itemBuilder: (context, i) {
                          final job = ws.jobs[i];
                          final id = "${job["id"] ?? ""}";
                          final label = (job["label"] ?? job["prompt"] ?? "Job").toString();
                          return Card(
                            color: const Color(0xFF1E293B),
                            margin: const EdgeInsets.only(bottom: 10),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: ListTile(
                              contentPadding: const EdgeInsets.symmetric(
                                horizontal: 14,
                                vertical: 8,
                              ),
                              title: Text(
                                label,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              subtitle: Padding(
                                padding: const EdgeInsets.only(top: 6),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      "[$id]  ${_fmtWhen(job)}",
                                      style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 12),
                                    ),
                                    if ((job["last_error"] ?? "").toString().isNotEmpty)
                                      Padding(
                                        padding: const EdgeInsets.only(top: 4),
                                        child: Text(
                                          "Last error: ${job["last_error"]}",
                                          style: const TextStyle(color: Colors.redAccent, fontSize: 11),
                                        ),
                                      ),
                                    if ((job["attempts"] ?? 0) != 0)
                                      Text(
                                        "Attempts: ${job["attempts"]}",
                                        style: TextStyle(color: Colors.white.withOpacity(0.45), fontSize: 11),
                                      ),
                                  ],
                                ),
                              ),
                              trailing: canCancelScheduledJob(job)
                                  ? TextButton(
                                      onPressed: id.isEmpty
                                          ? null
                                          : () async {
                                              final ok = await showDialog<bool>(
                                                context: context,
                                                builder: (ctx) => AlertDialog(
                                                  backgroundColor: const Color(0xFF1E293B),
                                                  title: const Text("Cancel job?",
                                                      style: TextStyle(color: Colors.white)),
                                                  content: Text(
                                                    label,
                                                    style: const TextStyle(color: Colors.white70),
                                                  ),
                                                  actions: [
                                                    TextButton(
                                                      onPressed: () => Navigator.pop(ctx, false),
                                                      child: const Text("Keep"),
                                                    ),
                                                    TextButton(
                                                      onPressed: () => Navigator.pop(ctx, true),
                                                      child: const Text(
                                                        "Cancel job",
                                                        style: TextStyle(color: Colors.redAccent),
                                                      ),
                                                    ),
                                                  ],
                                                ),
                                              );
                                              if (ok == true) {
                                                ws.cancelSchedule(id);
                                              }
                                            },
                                      child: const Text(
                                        "Cancel",
                                        style: TextStyle(color: Colors.redAccent),
                                      ),
                                    )
                                  : null,
                            ),
                          );
                        },
                      ),
          ),
          Expanded(
            flex: 2,
            child: Container(
              width: double.infinity,
              color: const Color(0xFF111C30),
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          "Recent execution history",
                          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
                        ),
                      ),
                      IconButton(
                        tooltip: "Refresh history",
                        onPressed: ws.isConnected ? ws.requestScheduleHistory : null,
                        icon: const Icon(Icons.refresh, color: Colors.white70, size: 20),
                      ),
                    ],
                  ),
                  Expanded(
                    child: !ws.isConnected
                        ? const Center(child: Text("Connect to desktop to view history", style: TextStyle(color: Colors.white54)))
                        : ws.scheduleHistory.isEmpty
                            ? const Center(child: Text("No execution history yet", style: TextStyle(color: Colors.white54)))
                            : ListView.builder(
                                itemCount: ws.scheduleHistory.length,
                                itemBuilder: (context, index) {
                                  final record = ws.scheduleHistory[index];
                                  return ListTile(
                                    dense: true,
                                    contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                                    title: Text(
                                      scheduledHistoryTitle(record),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600),
                                    ),
                                    subtitle: Text(
                                      scheduledHistorySubtitle(record),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(color: Colors.white54, fontSize: 10),
                                    ),
                                  );
                                },
                              ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
