//! Keycloak SSO authentication for the IAMDS ecosystem.
//!
//! Opens a Tauri WebviewWindow to the Keycloak OIDC login page, captures the
//! OAuth2 authorization code via a loopback HTTP server (RFC 8252 §7.3), and
//! exchanges the code for an access token containing the LiteLLM virtual key
//! in the JWT `"key"` claim.
//!
//! ## Flow
//! 1. Bind `TcpListener` on `127.0.0.1:0` — OS assigns a free port.
//! 2. Open WebviewWindow → `{base_url}/auth/realms/{realm}/protocol/openid-connect/auth`
//!    with `redirect_uri=http://127.0.0.1:{port}/callback` and a PKCE S256 challenge.
//! 3. Keycloak redirects the embedded browser to the loopback callback URL.
//! 4. Loopback server reads the HTTP request, extracts `?code=`, responds with
//!    a success page, and closes the window.
//! 5. Exchange `code` + `code_verifier` for tokens at the Keycloak token endpoint.
//! 6. Decode JWT payload (base64url) → read `"key"` claim → return as `api_key`.
//!
//! Using a loopback redirect avoids custom URL schemes (`hermes://`) which
//! macOS WKWebView does not intercept via `on_navigation` — the OS would
//! instead try to "download" the URL, creating a zero-byte file in Downloads.

use std::sync::{Arc, Mutex};

use sha2::{Digest, Sha256};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::oneshot;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize, Deserialize)]
pub struct KeycloakLoginResult {
    pub api_key: String,
    pub base_url: String,
}

// ---------------------------------------------------------------------------
// Tauri command
// ---------------------------------------------------------------------------

/// Open a Keycloak login window and exchange the resulting auth code for an
/// access token whose `"key"` JWT claim is the user's LiteLLM virtual key.
///
/// Uses a loopback HTTP redirect (RFC 8252 §7.3) so no custom URL scheme is
/// needed: a random free port on 127.0.0.1 is bound before the window opens,
/// and Keycloak redirects the browser to `http://127.0.0.1:{port}/callback`
/// where our local server captures the authorization code.
///
/// Parameters:
/// - `base_url`  — IAMDS ecosystem base URL, e.g. `https://suite.example.com`
/// - `realm`     — Keycloak realm name (default: `"aimds"`)
/// - `client_id` — Keycloak client ID (default: `"hermes-app"`)
#[tauri::command]
pub async fn keycloak_login(
    app: AppHandle,
    base_url: String,
    realm: String,
    client_id: String,
) -> Result<KeycloakLoginResult, String> {
    let base = base_url.trim_end_matches('/').to_string();
    let realm = if realm.trim().is_empty() { "aimds".to_string() } else { realm.trim().to_string() };
    let client_id = if client_id.trim().is_empty() { "hermes-app".to_string() } else { client_id.trim().to_string() };

    // Bind a loopback listener on a random free OS port (RFC 8252 §7.3).
    // This avoids custom URL schemes which macOS WKWebView does not intercept
    // via on_navigation — it would instead try to download hermes://... as a file.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|e| format!("Failed to bind loopback callback server: {e}"))?;
    let port = listener.local_addr()
        .map_err(|e| format!("Failed to get loopback callback port: {e}"))?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");

    // Generate PKCE code_verifier (32 random bytes → base64url, RFC 7636 §4.1)
    let code_verifier = generate_code_verifier();
    let code_challenge = compute_code_challenge(&code_verifier);

    let auth_url = build_auth_url(&base, &realm, &client_id, &redirect_uri, &code_challenge)?;

    tracing::info!(realm, client_id, port, "Starting Keycloak SSO login (loopback)");

    // oneshot channel: loopback server / window-close → async command
    let (tx, rx) = oneshot::channel::<Result<String, String>>();
    let tx_shared = Arc::new(Mutex::new(Some(tx)));
    let tx_server = tx_shared.clone();
    let tx_close = tx_shared.clone();

    // Unique window label so parallel logins don't collide.
    let win_label = format!("keycloak-auth-{}", uuid::Uuid::new_v4().simple());
    let win_label_close = win_label.clone();

    let parsed_url = auth_url
        .parse::<tauri::Url>()
        .map_err(|e| format!("Invalid Keycloak auth URL: {e}"))?;

    let win = WebviewWindowBuilder::new(&app, &win_label, WebviewUrl::External(parsed_url))
        .title("Sign in with Keycloak")
        .inner_size(500.0, 660.0)
        .center()
        .build()
        .map_err(|e| format!("Failed to open Keycloak auth window: {e}"))?;

    // Detect user closing the window without completing login.
    win.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed = event {
            if let Ok(mut guard) = tx_close.lock() {
                if let Some(sender) = guard.take() {
                    let _ = sender.send(Err("Login window closed before authentication completed".to_string()));
                }
            }
        }
    });

    // Spawn loopback server: accepts exactly one HTTP request, extracts the
    // auth code from the callback URL, serves a success page, then signals.
    let app_for_close = app.clone();
    tokio::spawn(async move {
        let result = accept_one_callback(listener).await;
        // Close the auth window (best-effort — it may already be closing).
        if let Some(w) = app_for_close.get_webview_window(&win_label_close) {
            let _ = w.close();
        }
        if let Ok(mut guard) = tx_server.lock() {
            if let Some(sender) = guard.take() {
                let _ = sender.send(result);
            }
        }
    });

    // Wait for the authorization code (5-minute timeout)
    let code_result = tokio::time::timeout(std::time::Duration::from_secs(300), rx)
        .await
        .map_err(|_| "Keycloak authentication timed out after 5 minutes".to_string())?
        .map_err(|_| "Internal error: auth channel dropped".to_string())?;

    let code = code_result?;
    tracing::info!("Keycloak auth code received, exchanging for token");

    let api_key = exchange_code_for_key(&base, &realm, &client_id, &code, &redirect_uri, &code_verifier).await?;
    tracing::info!("Keycloak virtual key extracted successfully");

    Ok(KeycloakLoginResult { api_key, base_url: base })
}

// ---------------------------------------------------------------------------
// Loopback callback server
// ---------------------------------------------------------------------------

/// Accept one HTTP request on `listener`, extract the `code` query parameter,
/// and respond with a success page the user sees before the window auto-closes.
async fn accept_one_callback(listener: tokio::net::TcpListener) -> Result<String, String> {
    let (mut stream, _) = listener
        .accept()
        .await
        .map_err(|e| format!("Callback server accept error: {e}"))?;

    let mut buf = vec![0u8; 8192];
    let n = stream
        .read(&mut buf)
        .await
        .map_err(|e| format!("Callback server read error: {e}"))?;

    let request = std::str::from_utf8(&buf[..n])
        .unwrap_or("")
        .to_string();

    // First line: "GET /callback?code=xxx&... HTTP/1.1"
    let first_line = request.lines().next().unwrap_or("");
    let path = first_line.split_whitespace().nth(1).unwrap_or("");
    let query = path.splitn(2, '?').nth(1).unwrap_or("");

    // Parse query params for code / error.
    let mut code: Option<String> = None;
    let mut error: Option<String> = None;
    for pair in query.split('&') {
        let mut kv = pair.splitn(2, '=');
        let k = kv.next().unwrap_or("");
        let v = kv.next().unwrap_or("").replace('+', " ");
        let v = percent_decode(&v);
        match k {
            "code" => code = Some(v),
            "error_description" => error = Some(v),
            "error" if error.is_none() => error = Some(v),
            _ => {}
        }
    }

    // Send HTTP response before returning so the browser renders the page.
    let (status, body) = if code.is_some() {
        ("200 OK", "<html><head><title>Hermes</title>\
            <style>body{font-family:system-ui;display:flex;align-items:center;\
            justify-content:center;height:100vh;margin:0;background:#f9fafb}\
            .card{text-align:center;padding:2rem;border-radius:1rem;\
            box-shadow:0 4px 24px #0001;background:#fff}\
            h2{color:#16a34a;margin-bottom:.5rem}p{color:#6b7280}</style></head>\
            <body><div class=\"card\"><h2>✓ Signed in</h2>\
            <p>You can close this window — Hermes is finishing the setup.</p>\
            </div></body></html>")
    } else {
        ("400 Bad Request", "<html><body><p>Authentication failed. You may close this window.</p></body></html>")
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n\
         Content-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        len = body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.flush().await;

    match code {
        Some(c) => Ok(c),
        None => Err(error.unwrap_or_else(|| "No authorization code in callback".to_string())),
    }
}

/// Minimal percent-decoder for callback query values (handles %XX sequences).
fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(hex) = std::str::from_utf8(&bytes[i + 1..i + 3]) {
                if let Ok(b) = u8::from_str_radix(hex, 16) {
                    out.push(b as char);
                    i += 3;
                    continue;
                }
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

// ---------------------------------------------------------------------------
// Token exchange + JWT decode
// ---------------------------------------------------------------------------

async fn exchange_code_for_key(
    base_url: &str,
    realm: &str,
    client_id: &str,
    code: &str,
    redirect_uri: &str,
    code_verifier: &str,
) -> Result<String, String> {
    let token_url = format!(
        "{base_url}/auth/realms/{realm}/protocol/openid-connect/token"
    );

    let client = reqwest::Client::new();
    let response = client
        .post(&token_url)
        .form(&[
            ("grant_type", "authorization_code"),
            ("client_id", client_id),
            ("code", code),
            ("redirect_uri", redirect_uri),
            ("code_verifier", code_verifier),
        ])
        .send()
        .await
        .map_err(|e| format!("Keycloak token exchange request failed: {e}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        tracing::error!(%status, %body, %token_url, realm, "Keycloak token endpoint error");
        return Err(format!("Keycloak token endpoint returned HTTP {status}: {body}"));
    }

    let token_data: serde_json::Value = response
        .json()
        .await
        .map_err(|e| format!("Failed to parse Keycloak token response: {e}"))?;

    let access_token = token_data["access_token"]
        .as_str()
        .ok_or_else(|| "No access_token in Keycloak response".to_string())?;

    decode_jwt_key(access_token)
}

/// Decode the JWT payload (base64url, middle section) and extract the `"key"` claim.
fn decode_jwt_key(token: &str) -> Result<String, String> {
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() < 2 {
        return Err("Invalid JWT: expected header.payload.signature".to_string());
    }

    let decoded = decode_base64url(parts[1])
        .map_err(|e| format!("Failed to base64url-decode JWT payload: {e}"))?;

    let payload: serde_json::Value = serde_json::from_slice(&decoded)
        .map_err(|e| format!("Failed to parse JWT payload as JSON: {e}"))?;

    payload["key"]
        .as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| {
            "No 'key' claim found in Keycloak JWT. Ensure the LiteLLM virtual key \
             is mapped as a 'key' token claim in the Keycloak realm."
                .to_string()
        })
}

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

fn build_auth_url(base_url: &str, realm: &str, client_id: &str, redirect_uri: &str, code_challenge: &str) -> Result<String, String> {
    Ok(format!(
        "{base_url}/auth/realms/{realm}/protocol/openid-connect/auth\
         ?client_id={encoded_client_id}\
         &response_type=code\
         &scope=openid\
         &redirect_uri={encoded_redirect}\
         &code_challenge={code_challenge}\
         &code_challenge_method=S256",
        encoded_client_id = percent_encode(client_id),
        encoded_redirect = percent_encode(redirect_uri),
    ))
}

/// Generate a PKCE code_verifier: 32 cryptographically random bytes, base64url-encoded
/// (RFC 7636 §4.1 — output is 43 characters, well within the 43–128 char range).
fn generate_code_verifier() -> String {
    let a = uuid::Uuid::new_v4();
    let b = uuid::Uuid::new_v4();
    let mut bytes = [0u8; 32];
    bytes[..16].copy_from_slice(a.as_bytes());
    bytes[16..].copy_from_slice(b.as_bytes());
    encode_base64url(&bytes)
}

/// Compute PKCE code_challenge = BASE64URL(SHA-256(ASCII(code_verifier))) (RFC 7636 §4.2).
fn compute_code_challenge(code_verifier: &str) -> String {
    let hash = Sha256::digest(code_verifier.as_bytes());
    encode_base64url(&hash)
}

/// Base64url-encode bytes without padding (RFC 4648 §5).
fn encode_base64url(input: &[u8]) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity((input.len() * 4 + 2) / 3);
    let mut i = 0;
    while i + 2 < input.len() {
        let n = ((input[i] as u32) << 16) | ((input[i + 1] as u32) << 8) | (input[i + 2] as u32);
        out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
        out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
        out.push(ALPHABET[((n >> 6) & 63) as usize] as char);
        out.push(ALPHABET[(n & 63) as usize] as char);
        i += 3;
    }
    match input.len() - i {
        1 => {
            let n = (input[i] as u32) << 16;
            out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
            out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
        }
        2 => {
            let n = ((input[i] as u32) << 16) | ((input[i + 1] as u32) << 8);
            out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
            out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
            out.push(ALPHABET[((n >> 6) & 63) as usize] as char);
        }
        _ => {}
    }
    out
}



fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Minimal base64url decoder (RFC 4648 §5, no external crate needed).
fn decode_base64url(input: &str) -> Result<Vec<u8>, String> {
    // base64url → standard base64, then add padding
    let mut b64: String = input.chars().map(|c| match c { '-' => '+', '_' => '/', c => c }).collect();
    match b64.len() % 4 {
        2 => b64.push_str("=="),
        3 => b64.push('='),
        _ => {}
    }
    decode_base64_std(&b64)
}

fn decode_base64_std(input: &str) -> Result<Vec<u8>, String> {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut table = [0xFFu8; 256];
    for (i, &c) in ALPHABET.iter().enumerate() {
        table[c as usize] = i as u8;
    }

    let input = input.trim_end_matches('=').as_bytes();
    let mut out = Vec::with_capacity(input.len() * 3 / 4 + 1);
    let mut buf = 0u32;
    let mut bits = 0u8;

    for &b in input {
        let val = table[b as usize];
        if val == 0xFF {
            return Err(format!("Invalid base64 character: {}", b as char));
        }
        buf = (buf << 6) | val as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((buf >> bits) & 0xFF) as u8);
        }
    }

    Ok(out)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_base64url_hello() {
        // "hello" → "aGVsbG8" in base64url
        let decoded = decode_base64url("aGVsbG8").unwrap();
        assert_eq!(decoded, b"hello");
    }

    #[test]
    fn decode_jwt_key_extracts_claim() {
        // Build a fake JWT with a "key" claim in the payload
        // Payload: {"key": "sk-test-abc123"}
        let payload_json = r#"{"sub":"user1","key":"sk-test-abc123","iss":"https://example.com"}"#;
        // base64url-encode the payload
        let encoded_payload = {
            let b64 = base64_encode_std(payload_json.as_bytes());
            b64.replace('+', "-").replace('/', "_").trim_end_matches('=').to_string()
        };
        let fake_token = format!("fakeheader.{encoded_payload}.fakesig");
        let result = decode_jwt_key(&fake_token).unwrap();
        assert_eq!(result, "sk-test-abc123");
    }

    #[test]
    fn decode_jwt_key_missing_claim_returns_error() {
        let payload_json = r#"{"sub":"user1","iss":"https://example.com"}"#;
        let encoded_payload = {
            let b64 = base64_encode_std(payload_json.as_bytes());
            b64.replace('+', "-").replace('/', "_").trim_end_matches('=').to_string()
        };
        let fake_token = format!("fakeheader.{encoded_payload}.fakesig");
        assert!(decode_jwt_key(&fake_token).is_err());
    }

    #[test]
    fn percent_encode_special_chars() {
        assert_eq!(percent_encode("https://example.com/cb"), "https%3A%2F%2Fexample.com%2Fcb");
        assert_eq!(percent_encode("abc-123_~."), "abc-123_~.");
    }

    #[test]
    fn build_auth_url_contains_required_params() {
        let url = build_auth_url(
            "https://suite.example.com",
            "aimds",
            "hermes-app",
            "http://127.0.0.1:54321/callback",
            "abc123challenge",
        ).unwrap();
        assert!(url.contains("client_id=hermes-app"));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("scope=openid"));
        assert!(url.contains("code_challenge=abc123challenge"));
        assert!(url.contains("code_challenge_method=S256"));
        assert!(url.contains("/auth/realms/aimds/protocol/openid-connect/auth"));
        assert!(url.contains("127.0.0.1"));
    }

    #[test]
    fn pkce_code_challenge_is_deterministic() {
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        // Known SHA-256 base64url for this verifier (RFC 7636 Appendix B)
        let challenge = compute_code_challenge(verifier);
        assert_eq!(challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    }

    #[test]
    fn generate_code_verifier_length() {
        let verifier = generate_code_verifier();
        // 32 bytes → 43 base64url chars (no padding)
        assert_eq!(verifier.len(), 43);
        assert!(verifier.chars().all(|c| c.is_alphanumeric() || c == '-' || c == '_'));
    }

    // Helper for test: standard base64 encode
    fn base64_encode_std(input: &[u8]) -> String {
        const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        let mut i = 0;
        while i + 2 < input.len() {
            let (a, b, c) = (input[i] as u32, input[i+1] as u32, input[i+2] as u32);
            let n = (a << 16) | (b << 8) | c;
            out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
            out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
            out.push(ALPHABET[((n >> 6) & 63) as usize] as char);
            out.push(ALPHABET[(n & 63) as usize] as char);
            i += 3;
        }
        let rem = &input[i..];
        match rem.len() {
            1 => {
                let n = (rem[0] as u32) << 16;
                out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
                out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
                out.push_str("==");
            }
            2 => {
                let n = ((rem[0] as u32) << 16) | ((rem[1] as u32) << 8);
                out.push(ALPHABET[((n >> 18) & 63) as usize] as char);
                out.push(ALPHABET[((n >> 12) & 63) as usize] as char);
                out.push(ALPHABET[((n >> 6) & 63) as usize] as char);
                out.push('=');
            }
            _ => {}
        }
        out
    }
}
