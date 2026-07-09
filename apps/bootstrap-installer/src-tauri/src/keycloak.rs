//! Keycloak SSO authentication for the IAMDS ecosystem.
//!
//! Opens a Tauri WebviewWindow to the Keycloak OIDC login page, intercepts the
//! OAuth2 authorization-code redirect via Tauri's `on_navigation` hook (before
//! the page loads), exchanges the code for an access token, and extracts the
//! LiteLLM virtual key from the JWT `"key"` claim.
//!
//! ## Flow
//! 1. Open WebviewWindow → `{base_url}/auth/realms/{realm}/protocol/openid-connect/auth`
//!    using client_id=hermes-app (dedicated public Keycloak client) and
//!    redirect_uri=`hermes://oauth/callback` (registered custom URI scheme).
//!    A PKCE code_challenge (S256) is sent with every authorization request.
//! 2. `on_navigation` intercepts the redirect to the callback URL before it loads
//! 3. Exchange `?code=` + `code_verifier` for tokens at the Keycloak token endpoint
//! 4. Decode JWT payload (base64url) → read `"key"` claim → return as `api_key`

use std::sync::{Arc, Mutex};

use sha2::{Digest, Sha256};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
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
/// Parameters:
/// - `base_url`     — IAMDS ecosystem base URL, e.g. `https://suite.example.com`
/// - `realm`        — Keycloak realm name (default: `"aimds"`)
/// - `client_id`    — Keycloak client ID (default: `"hermes-app"`)
/// - `redirect_uri` — OAuth redirect URI; defaults to `hermes://oauth/callback`
///                    which must be registered as a valid redirect URI in the
///                    `hermes-app` Keycloak client. Intercepted via on_navigation
///                    before the webview loads it.
#[tauri::command]
pub async fn keycloak_login(
    app: AppHandle,
    base_url: String,
    realm: String,
    client_id: String,
    redirect_uri: String,
) -> Result<KeycloakLoginResult, String> {
    let base = base_url.trim_end_matches('/').to_string();
    let realm = if realm.trim().is_empty() { "aimds".to_string() } else { realm.trim().to_string() };
    let client_id = if client_id.trim().is_empty() { "hermes-app".to_string() } else { client_id.trim().to_string() };
    let redirect_uri = if redirect_uri.trim().is_empty() {
        "hermes://oauth/callback".to_string()
    } else {
        redirect_uri.trim().to_string()
    };

    // Generate PKCE code_verifier (32 random bytes → base64url, RFC 7636 §4.1)
    let code_verifier = generate_code_verifier();
    let code_challenge = compute_code_challenge(&code_verifier);

    let auth_url = build_auth_url(&base, &realm, &client_id, &redirect_uri, &code_challenge)?;

    tracing::info!(realm, client_id, %redirect_uri, "Starting Keycloak SSO login");

    // oneshot channel: navigation hook / window-close → async command
    let (tx, rx) = oneshot::channel::<Result<String, String>>();
    let tx_shared = Arc::new(Mutex::new(Some(tx)));
    let tx_nav = tx_shared.clone();
    let tx_close = tx_shared.clone();
    let redirect_prefix = redirect_uri.clone();

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
        .on_navigation(move |url| {
            let url_str = url.as_str();

            // Intercept when Keycloak redirects to the registered callback URI.
            // Return false to block the page from loading — Open-WebUI never sees the code.
            if url_str.starts_with(&redirect_prefix) {
                let code = url
                    .query_pairs()
                    .find(|(k, _)| k == "code")
                    .map(|(_, v)| v.into_owned());

                let result = code.ok_or_else(|| {
                    let err = url
                        .query_pairs()
                        .find(|(k, _)| k == "error_description")
                        .or_else(|| url.query_pairs().find(|(k, _)| k == "error"))
                        .map(|(_, v)| v.into_owned())
                        .unwrap_or_else(|| "No authorization code in callback".to_string());
                    tracing::error!(error = %err, callback_url = %url_str, "Keycloak auth callback returned error");
                    err
                });

                if let Ok(mut guard) = tx_nav.lock() {
                    if let Some(sender) = guard.take() {
                        let _ = sender.send(result);
                    }
                }
                return false; // block — do not load the redirect page
            }

            true // allow all other navigations (Keycloak login pages)
        })
        .build()
        .map_err(|e| format!("Failed to open Keycloak auth window: {e}"))?;

    // Detect user closing the window without completing login
    win.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed = event {
            if let Ok(mut guard) = tx_close.lock() {
                if let Some(sender) = guard.take() {
                    let _ = sender.send(Err("Login window closed before authentication completed".to_string()));
                }
            }
        }
    });

    // Wait for the authorization code (5-minute timeout)
    let code_result = tokio::time::timeout(std::time::Duration::from_secs(300), rx)
        .await
        .map_err(|_| "Keycloak authentication timed out after 5 minutes".to_string())?
        .map_err(|_| "Internal error: auth channel dropped".to_string())?;

    // Close the auth window (best-effort — it may already be closing)
    if let Some(win_handle) = app.get_webview_window(&win_label_close) {
        let _ = win_handle.close();
    }

    let code = code_result?;
    tracing::info!("Keycloak auth code received, exchanging for token");

    let api_key = exchange_code_for_key(&base, &realm, &client_id, &code, &redirect_uri, &code_verifier).await?;
    tracing::info!("Keycloak virtual key extracted successfully");

    Ok(KeycloakLoginResult { api_key, base_url: base })
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
            "hermes://oauth/callback",
            "abc123challenge",
        ).unwrap();
        assert!(url.contains("client_id=hermes-app"));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("scope=openid"));
        assert!(url.contains("code_challenge=abc123challenge"));
        assert!(url.contains("code_challenge_method=S256"));
        assert!(url.contains("/auth/realms/aimds/protocol/openid-connect/auth"));
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
