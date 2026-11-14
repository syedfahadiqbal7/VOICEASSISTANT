import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/io.dart';

class WebSocketService extends ChangeNotifier {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _connectTimeout;

  bool isConnected = false;
  bool isConnecting = false;
  String statusMessage = "Disconnected";
  String statusColor = "#E74C3C";
  String? lastError;

  // Security Gate
  bool showSecurityGate = false;
  String securityText = "";
  String securityRisk = "low";
  String? securityRequestId;

  // Chat messages
  final List<Map<String, dynamic>> messages = [];

  // Session state (desktop → mobile)
  Map<String, dynamic> sessionState = {};
  bool undoAvailable = false;
  int jobsCount = 0;
  List<Map<String, dynamic>> jobs = [];
  String? lastUserText;
  String? lastStatus;
  Map<String, dynamic>? activePlan;

  Function(String)? onSpeak;

  void connect(String host, {int port = 8765, String token = ""}) {
    disconnect();

    host = host.trim();
    if (host.isEmpty) {
      statusMessage = "Enter desktop IP";
      statusColor = "#E74C3C";
      lastError = "Host empty";
      notifyListeners();
      return;
    }

    late final Uri endpoint;
    try {
      endpoint = Uri.parse(
        host.contains('://') ? host : "ws://$host:$port/ws",
      );
      if (!const {"ws", "wss"}.contains(endpoint.scheme) || endpoint.host.isEmpty) {
        throw const FormatException("Use a hostname/IP or a ws:// / wss:// URL");
      }
    } catch (e) {
      statusMessage = "Invalid desktop address: $e";
      statusColor = "#E74C3C";
      lastError = statusMessage;
      notifyListeners();
      return;
    }
    final url = (endpoint.path.isEmpty || endpoint.path == "/")
        ? endpoint.replace(path: "/ws").toString()
        : endpoint.toString();
    isConnecting = true;
    isConnected = false;
    statusMessage = "Connecting...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();

    try {
      _channel = IOWebSocketChannel.connect(
        Uri.parse(url),
        headers: token.trim().isEmpty
            ? null
            : {"x-voiceassistant-token": token.trim()},
        connectTimeout: const Duration(seconds: 5),
        pingInterval: const Duration(seconds: 20),
      );

      _sub = _channel!.stream.listen(
        (data) {
          _handleMessage(data);
        },
        onError: (error) {
          debugPrint("WS error: $error");
          _failConnect("Connection error (is desktop app running?)");
        },
        onDone: () {
          if (isConnected || isConnecting) {
            isConnected = false;
            isConnecting = false;
            showSecurityGate = false;
            securityText = "";
            securityRequestId = null;
            statusMessage = "Disconnected";
            statusColor = "#E74C3C";
            notifyListeners();
          }
        },
        cancelOnError: true,
      );

      // Do NOT mark Online until server sends type=connected
      _connectTimeout?.cancel();
      _connectTimeout = Timer(const Duration(seconds: 5), () {
        if (!isConnected && isConnecting) {
          _failConnect("No response from desktop (check IP / app running on :8765)");
        }
      });
    } catch (e) {
      _failConnect("Failed to connect: $e");
    }
  }

  void _failConnect(String msg) {
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    try {
      _channel?.sink.close();
    } catch (_) {}
    _channel = null;
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    statusMessage = msg;
    statusColor = "#E74C3C";
    lastError = msg;
    notifyListeners();
  }

  void disconnect() {
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    try {
      _channel?.sink.close();
    } catch (_) {}
    _channel = null;
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    statusMessage = "Disconnected";
    statusColor = "#E74C3C";
    notifyListeners();
  }

  void sendMessage(String text) {
    if (!isConnected || _channel == null) return;
    _channel!.sink.add(jsonEncode({
      "type": "user_message",
      "text": text,
    }));
  }

  void sendApproval(bool approved) {
    if (!isConnected || _channel == null || securityRequestId == null) return;
    _channel!.sink.add(jsonEncode({
      "type": "approval_response",
      "request_id": securityRequestId,
      "approved": approved,
    }));
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    notifyListeners();
  }

  void requestSessionState() {
    _sendRaw({"type": "get_session_state"});
  }

  void requestSchedules() {
    _sendRaw({"type": "list_schedules"});
  }

  void cancelSchedule(String jobId) {
    _sendRaw({"type": "cancel_schedule", "job_id": jobId});
  }

  void _sendRaw(Map<String, dynamic> payload) {
    if (!isConnected || _channel == null) return;
    _channel!.sink.add(jsonEncode(payload));
  }

  void _applySessionState(Map<String, dynamic>? state) {
    if (state == null) return;
    sessionState = Map<String, dynamic>.from(state);
    undoAvailable = state["undo_available"] == true;
    jobsCount = (state["jobs_count"] is int)
        ? state["jobs_count"] as int
        : int.tryParse("${state["jobs_count"]}") ?? 0;
    lastUserText = state["last_user_text"]?.toString();
    lastStatus = state["last_status"]?.toString();
    final plan = state["active_plan"];
    activePlan = plan is Map ? Map<String, dynamic>.from(plan) : null;
    final rawJobs = state["jobs"];
    if (rawJobs is List) {
      jobs = rawJobs
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
      jobsCount = jobs.length;
    }
  }

  void _handleMessage(dynamic raw) {
    try {
      final data = jsonDecode(raw);
      final type = data["type"];

      switch (type) {
        case "connected":
          _connectTimeout?.cancel();
          _connectTimeout = null;
          isConnecting = false;
          isConnected = true;
          statusMessage = data["message"] ?? "Connected to VoiceAssistant";
          statusColor = "#27AE60";
          lastError = null;
          // Ask for state after real handshake
          Future.delayed(const Duration(milliseconds: 200), () {
            requestSessionState();
            requestSchedules();
          });
          break;

        case "status":
          statusMessage = data["message"] ?? statusMessage;
          statusColor = data["color"] ?? statusColor;
          break;

        case "transcript":
          messages.add({
            "role": data["role"] ?? "system",
            "text": data["text"] ?? "",
            "time": DateTime.now().toIso8601String(),
          });
          // Bound the in-memory transcript for long-running mobile sessions.
          if (messages.length > 500) {
            messages.removeRange(0, messages.length - 500);
          }
          break;

        case "speak":
          if (onSpeak != null) {
            onSpeak!(data["text"] ?? "");
          }
          break;

        case "security_gate":
          showSecurityGate = true;
          securityText = data["display_text"] ?? "";
          securityRisk = data["risk_level"] ?? "low";
          securityRequestId = data["request_id"];
          break;

        case "session_state":
          _applySessionState(
            data["state"] is Map
                ? Map<String, dynamic>.from(data["state"])
                : null,
          );
          break;

        case "schedules":
          final list = data["jobs"];
          if (list is List) {
            jobs = list
                .whereType<Map>()
                .map((e) => Map<String, dynamic>.from(e))
                .toList();
            jobsCount = jobs.length;
          }
          break;

        case "schedule_cancelled":
          break;

        case "error":
          lastError = data["message"];
          break;
      }

      notifyListeners();
    } catch (e) {
      debugPrint("Parse error: $e");
    }
  }

  void clearMessages() {
    messages.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    _connectTimeout?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _connectTimeout = null;
    _sub = null;
    _channel = null;
    super.dispose();
  }
}
