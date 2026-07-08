//! Keycloak SSO authentication for the IAMDS ecosystem.
//!
//! Opens a Tauri WebviewWindow to the Keycloak OIDC login page, intercepts the
//! OAuth2 authorization-code redirect, exchanges the code for an access token,
//! and extracts the LiteLLM virtual key from the JWT `"key"` claim — all without
//! the user ever manually entering an API key.
//!
//! ## Flow
//! 1. Open WebviewWindow → `{base_url}/auth/realms/{realm}/protocol/openid-connect/auth`
//! 2. Intercept navigation to `redirect_uri` (default: `{base_url}/oauth/oidc/callback`)
//! 3. Exchange `?code=` for tokens at the Keycloak token endpoint
//! 4. Decode JWT payload (base64url) → read `"key"` claim → return as `api_key`

use std::sync::{Arc, Mutex};

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
/// - `redirect_uri` — OAuth redirect URI registered for the `open-webui` client;
///                    defaults to `{base_url}/oauth/oidc/callback` when empty
#[tauri::command]
pub async fn keycloak_login(
    app: AppHandle,
    base_url: String,
    realm: String,
    redirect_uri: String,
) -> Result<KeycloakLoginResult, String> {
    let base = base_url.trim_end_matches('/').to_string();
    let realm = if realm.trim().is_empty() { "aimds".to_string() } else { realm.trim().to_string() };

    // Derive the redirect URI from base_url when not explicitly provided.
    // Open-WebUI registers this path for its Keycloak OIDC client by default.
    let redirect_uri = if redirect_uri.trim().is_empty() {
        format!("{base}/oauth/oidc/callback")
    } else {
        redirect_uri.trim().to_string()
    };

    let auth_url = build_auth_url(&base, &realm, &redirect_uri)?;

    tracing::info!(realm, %redirect_uri, "Starting Keycloak SSO login");

    // oneshot channel: navigation hook → async command
    let (tx, rx) = oneshot::channel::<Result<String, String>>();
    let tx_shared = Arc::new(Mutex::new(Some(tx)));

    let tx_nav = tx_shared.clone();
    let redirect_prefix = redirect_uri.clone();

    // Unique window label so parallel logins don't collide.
    let win_label = format!("keycloak-auth-{}", uuid::Uuid::new_v4().simple());
    let win_label_close = win_label.clone();
    let tx_close = tx_shared.clone();

    let parsed_url = auth_url
        .parse::<tauri::Url>()
        .map_err(|e| format!("Invalid Keycloak auth URL: {e}"))?;

    let win = WebviewWindowBuilder::new(&app, &win_label, WebviewUrl::External(parsed_url))
        .title("Sign in with Keycloak")
        .inner_size(500.0, 660.0)
        .center()
        .on_navigation(move |url| {
            let url_str = url.as_str();

            // Intercept when Keycloak redirects to the registered redirect URI.
            if url_str.starts_with(&redirect_prefix) {
                let code = url
                    .query_pairs()
                    .find(|(k, _)| k == "code")
                    .map(|(_, v)| v.into_owned());

                let result = code.ok_or_else(|| {
                    // Could also be an error= param in the redirect
                    let err = url
                        .query_pairs()
                        .find(|(k, _)| k == "error_description")
                        .or_else(|| url.query_pairs().find(|(k, _)| k == "error"))
                        .map(|(_, v)| v.into_owned())
                        .unwrap_or_else(|| "No authorization code in callback".to_string());
                    err
                });

                if let Ok(mut guard) = tx_nav.lock() {
                    if let Some(sender) = guard.take() {
                        let _ = sender.send(result);
                    }
                }
                return false; // Block loading the redirect page — we own the response
            }

            true // Allow all other navigations (Keycloak login pages)
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

    let api_key = exchange_code_for_key(&base, &realm, &code, &redirect_uri).await?;
    tracing::info!("Keycloak virtual key extracted successfully");

    Ok(KeycloakLoginResult { api_key, base_url: base })
}

// ---------------------------------------------------------------------------
// Token exchange + JWT decode
// ---------------------------------------------------------------------------

async fn exchange_code_for_key(
    base_url: &str,
    realm: &str,
    code: &str,
    redirect_uri: &str,
) -> Result<String, String> {
    let token_url = format!(
        "{base_url}/auth/realms/{realm}/protocol/openid-connect/token"
    );

    let client = reqwest::Client::new();
    let response = client
        .post(&token_url)
        .form(&[
            ("grant_type", "authorization_code"),
            ("client_id", "open-webui"),
            ("code", code),
            ("redirect_uri", redirect_uri),
        ])
        .send()
        .await
        .map_err(|e| format!("Keycloak token exchange request failed: {e}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
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

fn build_auth_url(base_url: &str, realm: &str, redirect_uri: &str) -> Result<String, String> {
    Ok(format!(
        "{base_url}/auth/realms/{realm}/protocol/openid-connect/auth\
         ?client_id=open-webui\
         &response_type=code\
         &scope=openid\
         &redirect_uri={encoded_redirect}",
        encoded_redirect = percent_encode(redirect_uri)
    ))
}

/// Percent-encode a string for use as a URI query parameter value (RFC 3986).
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
        let url = build_auth_url("https://suite.example.com", "master", "https://suite.example.com/oauth/oidc/callback").unwrap();
        assert!(url.contains("client_id=open-webui"));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("scope=openid"));
        assert!(url.contains("/auth/realms/aimds/protocol/openid-connect/auth"));
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
