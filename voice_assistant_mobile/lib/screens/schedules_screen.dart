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
      }
    });
  }

  String _fmtWhen(Map<String, dynamic> job) {
    final repeat = job["repeat"];
    final rawStatus = job["status"]?.toString();
    final enabled = job["enabled"] != false;
    final waiting = job["execution_waiting"] == true;
    final status = waiting
        ? "WAITING"
        : (rawStatus == null || rawStatus.isEmpty
            ? (enabled ? "PENDING" : "DONE")
            : rawStatus.toUpperCase());
    final when = scheduledRunAtLabel(job);
    if (repeat is Map && repeat["every_minutes"] != null) {
      return "$status · every ${repeat["every_minutes"]} min · next $when";
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
        ],
      ),
    );
  }
}
