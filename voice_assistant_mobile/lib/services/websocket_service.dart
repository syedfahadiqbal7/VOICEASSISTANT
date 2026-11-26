import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/io.dart';

import 'mobile_identity_service.dart';

class WebSocketService extends ChangeNotifier {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _connectTimeout;
  Timer? _reconnectTimer;
  final FlutterSecureStorage _secureStorage = const FlutterSecureStorage();

  int _connectionGeneration = 0;
  int _reconnectAttempts = 0;
  bool _manualDisconnect = true;
  String? _lastHost;
  String _lastToken = "";
  String _lastDeviceId = "";
  int _lastPort = 8765;

  bool isConnected = false;
  bool isConnecting = false;
  bool isPairing = false;
  bool hasSavedConnection = false;
  String statusMessage = "Disconnected";
  String statusColor = "#E74C3C";
  String? lastError;
  String? securityWarning;
  String? lastUndoMessage;

  // Security Gate
  bool showSecurityGate = false;
  String securityText = "";
  String securityRisk = "low";
  String? securityRequestId;
  bool approvalSubmitting = false;
  Timer? _approvalTimeout;

  // Chat messages
  final List<Map<String, dynamic>> messages = [];

  // Session state (desktop → mobile)
  Map<String, dynamic> sessionState = {};
  bool undoAvailable = false;
  int jobsCount = 0;
  List<Map<String, dynamic>> jobs = [];
  List<Map<String, dynamic>> scheduleHistory = [];
  String? lastUserText;
  String? lastStatus;
  Map<String, dynamic>? activePlan;

  Function(String)? onSpeak;

  Future<Map<String, String>?> loadSavedConnection() async {
    try {
      final host = await _secureStorage.read(key: "desktop_host");
      if (host == null || host.trim().isEmpty) {
        hasSavedConnection = false;
        notifyListeners();
        return null;
      }
      final token = await _secureStorage.read(key: "mobile_token") ?? "";
      final deviceId = await _secureStorage.read(key: "mobile_device_id") ?? "";
      final autoConnect = await _secureStorage.read(key: "auto_connect") ?? "true";
      hasSavedConnection = true;
      notifyListeners();
      return {
        "host": host,
        "token": token,
        "device_id": deviceId,
        "auto_connect": autoConnect,
      };
    } catch (error) {
      lastError = "Could not read secure connection settings";
      debugPrint("Secure connection settings read failed: $error");
      notifyListeners();
      return null;
    }
  }

  Future<void> _persistConnection(int generation) async {
    final host = _lastHost;
    if (host == null ||
        host.isEmpty ||
        generation != _connectionGeneration ||
        _manualDisconnect) {
      return;
    }
    try {
      await _secureStorage.write(key: "desktop_host", value: host);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "mobile_token", value: _lastToken);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "mobile_device_id", value: _lastDeviceId);
      if (generation != _connectionGeneration || _manualDisconnect) return;
      await _secureStorage.write(key: "auto_connect", value: "true");
      if (generation != _connectionGeneration || _manualDisconnect) return;
      hasSavedConnection = true;
    } catch (error) {
      if (generation == _connectionGeneration && !_manualDisconnect) {
        lastError = "Connection works, but secure credential storage failed";
        debugPrint("Secure connection settings write failed: $error");
      }
    }
    if (generation == _connectionGeneration && !_manualDisconnect) {
      notifyListeners();
    }
  }

  Future<void> _setAutoConnect(bool enabled) async {
    try {
      await _secureStorage.write(key: "auto_connect", value: enabled ? "true" : "false");
    } catch (error) {
      debugPrint("Auto-connect preference update failed: $error");
    }
  }

  Future<void> forgetSavedConnection() async {
    // Forgetting credentials must also stop the live socket and its reconnect loop.
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;

    try {
      await _sub?.cancel();
    } catch (_) {}
    _sub = null;
    try {
      await _channel?.sink.close();
    } catch (_) {}
    _channel = null;

    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    statusMessage = "Disconnected";
    statusColor = "#E74C3C";
    _lastHost = null;
    _lastToken = "";
    _lastDeviceId = "";
    _lastPort = 8765;

    try {
      await _secureStorage.delete(key: "desktop_host");
      await _secureStorage.delete(key: "mobile_token");
      await _secureStorage.delete(key: "mobile_device_id");
      await _secureStorage.delete(key: "auto_connect");
      hasSavedConnection = false;
      lastError = null;
    } catch (error) {
      lastError = "Connection stopped, but saved credentials could not be removed";
      debugPrint("Secure connection settings delete failed: $error");
    }
    notifyListeners();
  }


  static Uri _validatePairingEndpoint(dynamic value) {
    if (value is! String || value.length > 2048) {
      throw const FormatException("Invalid QR pairing endpoint");
    }
    final endpoint = Uri.tryParse(value);
    if (endpoint == null ||
        endpoint.scheme != "https" ||
        endpoint.host.isEmpty ||
        endpoint.userInfo.isNotEmpty ||
        endpoint.hasQuery ||
        endpoint.hasFragment ||
        (endpoint.path.isNotEmpty && endpoint.path != "/")) {
      throw const FormatException("QR pairing requires a trusted HTTPS endpoint");
    }
    return endpoint;
  }

  static Future<Map<String, dynamic>> _postPairingJson(
    HttpClient client,
    Uri endpoint,
    Map<String, dynamic> payload,
  ) async {
    final request = await client.postUrl(endpoint).timeout(const Duration(seconds: 5));
    request.followRedirects = false;
    request.headers.contentType = ContentType.json;
    request.add(utf8.encode(jsonEncode(payload)));
    final response = await request.close().timeout(const Duration(seconds: 5));
    final bytes = await response.fold<List<int>>(<int>[], (buffer, chunk) {
      if (buffer.length + chunk.length > 16384) {
        throw const FormatException("Pairing server response is too large");
      }
      buffer.addAll(chunk);
      return buffer;
    }).timeout(const Duration(seconds: 5));
    final decoded = jsonDecode(utf8.decode(bytes));
    if (decoded is! Map) {
      throw const FormatException("Invalid pairing server response");
    }
    final result = Map<String, dynamic>.from(decoded);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw StateError(result["detail"]?.toString() ?? "Pairing request was rejected");
    }
    return result;
  }

  Future<void> pairFromQr(String rawPayload, {required String deviceName}) async {
    if (isPairing) return;
    if (deviceName.trim().isEmpty || deviceName.trim().length > 80) {
      throw ArgumentError("Enter a device name between 1 and 80 characters");
    }

    final decoded = jsonDecode(rawPayload);
    if (decoded is! Map ||
        decoded["type"] != "voiceassistant_mobile_pairing" ||
        decoded["version"] != 2) {
      throw const FormatException("This is not a VoiceAssistant pairing QR code");
    }
    final endpoint = _validatePairingEndpoint(decoded["endpoint"]);
    final sessionId = decoded["session_id"];
    final secret = decoded["secret"];
    final expiresRaw = decoded["expires_at"];
    if (sessionId is! String ||
        !RegExp(r"^[0-9a-f]{32}$").hasMatch(sessionId) ||
        secret is! String ||
        secret.length < 32 ||
        secret.length > 256 ||
        expiresRaw is! String) {
      throw const FormatException("QR pairing data is incomplete or invalid");
    }
    final expiresAt = DateTime.tryParse(expiresRaw);
    if (expiresAt == null ||
        !expiresAt.isAfter(DateTime.now()) ||
        expiresAt.isAfter(DateTime.now().add(const Duration(minutes: 6)))) {
      throw const FormatException("QR pairing code has expired or has an invalid lifetime");
    }

    final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
    isPairing = true;
    statusMessage = "Requesting desktop approval...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();
    try {
      final requestUri = endpoint.replace(path: "/pairing/request");
      final resultUri = endpoint.replace(path: "/pairing/result");
      // Generate the device signing identity only after the user explicitly
      // scans a pairing QR. The private seed remains in platform secure storage.
      final identity = await MobileIdentityService().loadOrCreateForPairing();
      final normalizedName = deviceName.trim();
      final proofMessage = utf8.encode(
        "voiceassistant-mobile-pairing-v2\n"
        "$sessionId\n"
        "$normalizedName\n"
        "${identity.publicKey}\n"
        "$secret",
      );
      final proofSignature = base64Url
          .encode(await MobileIdentityService().sign(proofMessage))
          .replaceAll("=", "");
      await _postPairingJson(client, requestUri, {
        "session_id": sessionId,
        "secret": secret,
        "device_name": normalizedName,
        "public_key_ed25519": identity.publicKey,
        "proof_signature": proofSignature,
      });

      while (DateTime.now().isBefore(expiresAt)) {
        await Future.delayed(const Duration(seconds: 1));
        final result = await _postPairingJson(client, resultUri, {
          "session_id": sessionId,
          "secret": secret,
        });
        final state = result["status"];
        if (state == "denied") {
          throw StateError("The desktop user denied this device pairing");
        }
        if (state == "approved") {
          final credentials = result["credentials"];
          if (credentials is! Map ||
              credentials["device_id"] is! String ||
              credentials["device_token"] is! String) {
            throw const FormatException("Desktop returned invalid device credentials");
          }
          final socketEndpoint = endpoint.replace(scheme: "wss", path: "/ws");
          final deviceId = credentials["device_id"] as String;
          final deviceToken = credentials["device_token"] as String;

          // Persist the received credential before acknowledging it. If the
          // app is terminated immediately after the acknowledgement, the user
          // can still reconnect without having to enroll a ghost device again.
          await _secureStorage.write(key: "desktop_host", value: socketEndpoint.toString());
          await _secureStorage.write(key: "mobile_token", value: deviceToken);
          await _secureStorage.write(key: "mobile_device_id", value: deviceId);
          await _secureStorage.write(key: "auto_connect", value: "false");
          hasSavedConnection = true;
          notifyListeners();

          // Acknowledge receipt before connecting. If this response is lost,
          // retrying the acknowledgement is safe and idempotent.
          Map<String, dynamic>? completion;
          for (var attempt = 0; attempt < 3; attempt++) {
            try {
              completion = await _postPairingJson(
                client,
                endpoint.replace(path: "/pairing/complete"),
                {"session_id": sessionId, "secret": secret},
              );
              break;
            } catch (_) {
              if (attempt == 2 || !DateTime.now().isBefore(expiresAt)) rethrow;
              await Future.delayed(const Duration(seconds: 1));
            }
          }
          if (completion?["status"] != "completed") {
            throw StateError("Desktop did not confirm credential receipt");
          }
          await _secureStorage.write(key: "auto_connect", value: "true");
          isPairing = false;
          notifyListeners();
          connect(
            socketEndpoint.toString(),
            deviceId: deviceId,
            token: deviceToken,
          );
          return;
        }
        if (state != "pending_approval" && state != "waiting") {
          throw StateError("Unexpected pairing status from desktop");
        }
        statusMessage = state == "pending_approval"
            ? "Waiting for approval on desktop..."
            : "Waiting for phone request...";
        notifyListeners();
      }
      throw StateError("Pairing code expired before desktop approval");
    } catch (error) {
      isPairing = false;
      statusMessage = "Pairing failed";
      statusColor = "#E74C3C";
      lastError = error.toString();
      notifyListeners();
      rethrow;
    } finally {
      client.close(force: true);
      if (isPairing) {
        isPairing = false;
        notifyListeners();
      }
    }
  }

  void connect(String host, {int port = 8765, String token = "", String deviceId = ""}) {
    _manualDisconnect = false;
    _lastHost = host.trim();
    _lastToken = token.trim();
    _lastDeviceId = deviceId.trim();
    _lastPort = port;
    _reconnectAttempts = 0;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _connectInternal();
  }

  void _connectInternal() {
    final generation = ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final previousChannel = _channel;
    _channel = null;
    try {
      previousChannel?.sink.close();
    } catch (_) {}

    final host = _lastHost?.trim() ?? "";
    if (host.isEmpty) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "Enter desktop IP";
      statusColor = "#E74C3C";
      lastError = "Host empty";
      notifyListeners();
      return;
    }

    late final Uri endpoint;
    try {
      endpoint = Uri.parse(
        host.contains('://') ? host : "ws://$host:$_lastPort/ws",
      );
      if (!const {"ws", "wss"}.contains(endpoint.scheme) || endpoint.host.isEmpty) {
        throw const FormatException("Use a hostname/IP or a ws:// / wss:// URL");
      }
    } catch (error) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "Invalid desktop address: $error";
      statusColor = "#E74C3C";
      lastError = statusMessage;
      notifyListeners();
      return;
    }

    final url = (endpoint.path.isEmpty || endpoint.path == "/")
        ? endpoint.replace(path: "/ws").toString()
        : endpoint.toString();
    final isLoopback = const {"localhost", "127.0.0.1", "::1"}.contains(endpoint.host);
    securityWarning = endpoint.scheme == "ws" && !isLoopback
        ? "Unencrypted WS connection. Use wss:// or a trusted VPN on LAN."
        : null;
    if (defaultTargetPlatform == TargetPlatform.iOS &&
        endpoint.scheme == "ws" &&
        !isLoopback) {
      isConnecting = false;
      isConnected = false;
      statusMessage = "iOS requires wss:// for remote desktop connections";
      statusColor = "#E74C3C";
      lastError = statusMessage;
      notifyListeners();
      return;
    }

    isConnecting = true;
    isConnected = false;
    statusMessage = "Connecting...";
    statusColor = "#F39C12";
    lastError = null;
    notifyListeners();

    try {
      final channel = IOWebSocketChannel.connect(
        Uri.parse(url),
        headers: _lastToken.isEmpty
            ? null
            : (_lastDeviceId.isNotEmpty
                ? {
                    "x-voiceassistant-device-id": _lastDeviceId,
                    "x-voiceassistant-device-token": _lastToken,
                  }
                : {"x-voiceassistant-token": _lastToken}),
        connectTimeout: const Duration(seconds: 5),
        pingInterval: const Duration(seconds: 20),
      );
      _channel = channel;

      _sub = channel.stream.listen(
        (data) {
          if (generation == _connectionGeneration) {
            _handleMessage(data);
          }
        },
        onError: (error) {
          if (generation != _connectionGeneration) return;
          debugPrint("WS error: $error");
          _failConnect(
            "Connection error (is desktop app running?)",
            generation: generation,
          );
        },
        onDone: () {
          if (generation != _connectionGeneration) return;
          final closeCode = channel.closeCode;
          final rejected = closeCode == 1008;
          _failConnect(
            rejected
                ? "Desktop rejected the mobile token. Check the saved token."
                : "Connection lost",
            generation: generation,
            retry: !rejected,
          );
        },
        cancelOnError: true,
      );

      _connectTimeout = Timer(const Duration(seconds: 5), () {
        if (generation == _connectionGeneration && !isConnected && isConnecting) {
          _failConnect(
            "No response from desktop (check IP / app running on :8765)",
            generation: generation,
          );
        }
      });
    } catch (error) {
      _failConnect(
        "Failed to connect: $error",
        generation: generation,
      );
    }
  }

  void _failConnect(
    String message, {
    required int generation,
    bool retry = true,
  }) {
    if (generation != _connectionGeneration) return;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final oldChannel = _channel;
    _channel = null;
    try {
      oldChannel?.sink.close();
    } catch (_) {}
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;
    statusMessage = message;
    statusColor = "#E74C3C";
    lastError = message;
    notifyListeners();
    if (retry) _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_manualDisconnect || _lastHost == null || _reconnectTimer != null) return;
    const delays = [1, 2, 4, 8, 16, 30];
    final delayIndex = math.min(_reconnectAttempts, delays.length - 1).toInt();
    final seconds = delays[delayIndex];
    _reconnectAttempts++;
    statusMessage = "Connection lost. Retrying in ${seconds}s...";
    statusColor = "#F39C12";
    notifyListeners();
    _reconnectTimer = Timer(Duration(seconds: seconds), () {
      _reconnectTimer = null;
      if (!_manualDisconnect && !isConnected && !isConnecting) {
        _connectInternal();
      }
    });
  }

  void disconnect() {
    _manualDisconnect = true;
    unawaited(_setAutoConnect(false));
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    ++_connectionGeneration;
    _connectTimeout?.cancel();
    _connectTimeout = null;
    try {
      _sub?.cancel();
    } catch (_) {}
    _sub = null;
    final oldChannel = _channel;
    _channel = null;
    try {
      oldChannel?.sink.close();
    } catch (_) {}
    isConnected = false;
    isConnecting = false;
    showSecurityGate = false;
    securityText = "";
    securityRequestId = null;
    approvalSubmitting = false;
    _approvalTimeout?.cancel();
    _approvalTimeout = null;
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
    if (!isConnected ||
        _channel == null ||
        securityRequestId == null ||
        approvalSubmitting) {
      return;
    }
    try {
      _channel!.sink.add(jsonEncode({
        "type": "approval_response",
        "request_id": securityRequestId,
        "approved": approved,
      }));
      approvalSubmitting = true;
      lastError = null;
      _approvalTimeout?.cancel();
      _approvalTimeout = Timer(const Duration(seconds: 10), () {
        if (securityRequestId != null && approvalSubmitting) {
          approvalSubmitting = false;
          lastError = "Desktop did not acknowledge the approval. Retry or reconnect.";
          notifyListeners();
        }
      });
      notifyListeners();
    } catch (error) {
      approvalSubmitting = false;
      lastError = "Could not send approval. Check the connection and retry.";
      notifyListeners();
    }
  }

  void requestSessionState() {
    _sendRaw({"type": "get_session_state"});
  }

  void requestSchedules() {
    _sendRaw({"type": "list_schedules"});
  }

  void requestScheduleHistory() {
    _sendRaw({"type": "list_schedule_history"});
  }

  void requestUndo() {
    _sendRaw({"type": "undo_last"});
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
      if (data is! Map) return;
      final type = data["type"];

      switch (type) {
        case "connected":
          _connectTimeout?.cancel();
          _connectTimeout = null;
          isConnecting = false;
          isConnected = true;
          _reconnectAttempts = 0;
          statusMessage = data["message"] ?? "Connected to VoiceAssistant";
          statusColor = "#27AE60";
          lastError = null;
          unawaited(_persistConnection(_connectionGeneration));
          Future.delayed(const Duration(milliseconds: 200), () {
            if (isConnected) {
              requestSessionState();
              requestSchedules();
              requestScheduleHistory();
            }
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
          _approvalTimeout?.cancel();
          approvalSubmitting = false;
          showSecurityGate = true;
          securityText = data["display_text"] ?? "";
          securityRisk = data["risk_level"] ?? "low";
          securityRequestId = data["request_id"]?.toString();
          break;

        case "approval_ack":
          if (data["request_id"]?.toString() == securityRequestId) {
            _approvalTimeout?.cancel();
            _approvalTimeout = null;
            approvalSubmitting = false;
            showSecurityGate = false;
            securityText = "";
            securityRequestId = null;
            if (data["accepted"] != true) {
              lastError = "Approval request expired or was rejected by the desktop session.";
            }
          }
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

        case "schedule_history":
          final records = data["records"];
          if (records is List) {
            scheduleHistory = records
                .whereType<Map>()
                .map((e) => Map<String, dynamic>.from(e))
                .toList();
          }
          break;

        case "undo_result":
          final result = data["result"];
          final ok = result is Map && result["ok"] == true;
          lastUndoMessage = ok
              ? (result["message"]?.toString() ?? "Undo completed")
              : (result is Map
                  ? (result["message"]?.toString() ?? "Undo failed")
                  : "Undo failed");
          if (!ok) lastError = lastUndoMessage;
          break;

        case "schedule_cancelled":
          lastStatus = data["message"]?.toString() ?? "Schedule updated";
          break;

        case "error":
          lastError = data["message"]?.toString();
          break;
      }

      notifyListeners();
    } catch (error) {
      debugPrint("Parse error: $error");
    }
  }

  void clearError() {
    if (lastError == null) return;
    lastError = null;
    notifyListeners();
  }

  void clearMessages() {
    messages.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    _manualDisconnect = true;
    _reconnectTimer?.cancel();
    _connectTimeout?.cancel();
    _approvalTimeout?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _reconnectTimer = null;
    _connectTimeout = null;
    _sub = null;
    _channel = null;
    super.dispose();
  }
}
