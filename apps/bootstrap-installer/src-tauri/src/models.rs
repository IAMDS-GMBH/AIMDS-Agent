//! Fetch available models from litellm endpoint and cache them.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

fn normalize_bootstrap_base_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("Base URL is required".to_string());
    }

    let candidate = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };

    let mut parsed = reqwest::Url::parse(&candidate)
        .map_err(|_| "Base URL is invalid".to_string())?;

    if parsed.host_str().is_none() {
        return Err("Base URL is invalid".to_string());
    }
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return Err("Base URL must be http(s)".to_string());
    }

    parsed
        .set_scheme("https")
        .map_err(|_| "Could not force https for Base URL".to_string())?;
    parsed.set_fragment(None);

    // Lowercase the host — hostnames are case-insensitive per RFC 4343
    if let Some(host) = parsed.host_str().map(|h| h.to_lowercase()) {
        parsed.set_host(Some(&host))
            .map_err(|_| "Failed to normalize hostname case".to_string())?;
    }

    let path = parsed.path().trim_end_matches('/').to_string();
    if path.is_empty() || path == "/" {
        parsed.set_path("");
    } else {
        parsed.set_path(&path);
    }

    Ok(parsed.to_string().trim_end_matches('/').to_string())
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ModelInfo {
    pub id: String,
    #[serde(flatten)]
    pub extra: serde_json::Value,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ModelsResponse {
    pub object: String,
    pub data: Vec<ModelInfo>,
}

/// Provider model cache entry (matches hermes-agent's structure).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderCacheEntry {
    pub fp: String,       // "pinned" or timestamp
    pub at: f64,          // timestamp
    pub models: Vec<String>,
}

/// Full provider models cache structure.
#[derive(Debug, Serialize, Deserialize)]
pub struct ProviderModelsCache {
    #[serde(flatten)]
    pub providers: std::collections::HashMap<String, ProviderCacheEntry>,
}

/// Fetch available models from a litellm endpoint (server-side).
///
/// This bypasses Tauri's ACL restrictions on client-side HTTP by doing the
/// fetch on the Rust side and returning the results to the frontend.
#[tauri::command]
pub async fn fetch_models(base_url: String, api_key: String) -> Result<Vec<String>, String> {
    let normalized_url = normalize_bootstrap_base_url(&base_url)?;
    let endpoint = format!("{}/litellm/v1/models", normalized_url);

    tracing::info!("Fetching models from: {}", endpoint);

    let client = reqwest::Client::new();
    let response = client
        .get(&endpoint)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .send()
        .await
        .map_err(|e| format!("Network error: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("HTTP {}: {}", response.status(), response.status().canonical_reason().unwrap_or("Unknown")));
    }

    let data: ModelsResponse = response
        .json()
        .await
        .map_err(|e| format!("Failed to parse response: {}", e))?;

    let mut models: Vec<String> = data
        .data
        .into_iter()
        .map(|m| m.id.trim().to_string())
        .filter(|id| !id.is_empty() && !id.starts_with('_'))
        .collect();
    models.sort();

    if models.is_empty() {
        return Err("No models found in response".to_string());
    }

    tracing::info!("Fetched {} models", models.len());
    Ok(models)
}

/// Check whether the LiteLLM endpoint is reachable.
///
/// Health is considered OK when:
/// - `/litellm/health` returns 2xx, or
/// - `/litellm/v1/models` returns 2xx or 401 (unauthorized but reachable)
#[tauri::command]
pub async fn check_litellm_health(base_url: String) -> bool {
    let normalized = match normalize_bootstrap_base_url(&base_url) {
        Ok(u) => u,
        Err(_) => return false,
    };

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };

    // Primary probe: /litellm/health
    let health_url = format!("{}/litellm/health", normalized);
    tracing::debug!("LiteLLM health check (primary): {}", health_url);
    if let Ok(r) = client.get(&health_url).send().await {
        if r.status().is_success() {
            return true;
        }
    }

    // Fallback probe: /litellm/v1/models — treat only 2xx or 401 as reachable
    let models_url = format!("{}/litellm/v1/models", normalized);
    tracing::debug!("LiteLLM health check (fallback): {}", models_url);
    if let Ok(r) = client.get(&models_url).send().await {
        return r.status().is_success() || r.status() == reqwest::StatusCode::UNAUTHORIZED;
    }

    false
}

/// Write provider_models_cache.json with fetched LiteLLM models pinned for OpenAI slugs.
///
/// Updates `~/.hermes/provider_models_cache.json` while preserving unrelated
/// provider cache entries (so reinstall does not wipe prior discovery state).
/// The installer pins `iamds-litellm`, `openai-api` and `openai` to the fetched
/// list and removes Copilot cache entries to keep the picker aligned with the
/// bootstrap LiteLLM endpoint.
#[tauri::command]
pub async fn write_provider_models_cache(
    hermes_home: Option<String>,
    model_names: Vec<String>,
) -> Result<(), String> {
    if model_names.is_empty() {
        return Err("No models provided".to_string());
    }

    // Determine HERMES_HOME path
    let hermes_home_path = if let Some(home) = hermes_home {
        PathBuf::from(home)
    } else {
        // Use the platform-correct default: %LOCALAPPDATA%\hermes on Windows,
        // ~/.hermes on macOS/Linux — mirrors paths::hermes_home() exactly.
        crate::paths::hermes_home()
    };

    let cache_file = hermes_home_path.join("provider_models_cache.json");

    // Ensure HERMES_HOME exists — on a fresh Windows install it won't be
    // created until install.ps1 runs later, so we must create it here or the
    // subsequent write will fail and the model cache won't be written.
    if let Err(e) = std::fs::create_dir_all(&hermes_home_path) {
        return Err(format!("Failed to create HERMES_HOME directory: {}", e));
    }

    // Load existing cache so reinstall/update keeps previously discovered
    // provider entries instead of blowing them away.
    let mut providers: std::collections::HashMap<String, ProviderCacheEntry> = if cache_file.exists()
    {
        match std::fs::read_to_string(&cache_file) {
            Ok(raw) => serde_json::from_str::<ProviderModelsCache>(&raw)
                .map(|c| c.providers)
                .unwrap_or_default(),
            Err(_) => std::collections::HashMap::new(),
        }
    } else {
        std::collections::HashMap::new()
    };

    // Keep the bootstrap UX opinionated: no Copilot catalog in this path.
    providers.remove("copilot");
    providers.remove("copilot-acp");

    let pinned_entry = ProviderCacheEntry {
        fp: "pinned".to_string(),
        at: 9999999999.0, // Far future timestamp to indicate this is pinned
        models: model_names,
    };

    providers.insert(
        "iamds-litellm".to_string(),
        pinned_entry.clone(),
    );
    providers.insert(
        "openai-api".to_string(),
        pinned_entry.clone(),
    );
    providers.insert(
        "openai".to_string(),
        pinned_entry,
    );

    let model_count = providers
        .get("iamds-litellm")
        .map(|entry| entry.models.len())
        .unwrap_or(0);
    let cache = ProviderModelsCache { providers };

    // Write cache file (overwrites any existing cache to ensure clean state)
    let json_str = serde_json::to_string(&cache)
        .map_err(|e| format!("Failed to serialize cache: {}", e))?;

    std::fs::write(&cache_file, json_str)
        .map_err(|e| format!("Failed to write cache file: {}", e))?;

    tracing::info!(
        "Wrote provider models cache to: {} (pinned iamds-litellm/openai/openai-api, {} models)",
        cache_file.display(),
        model_count
    );
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ExistingConfig {
    pub base_url: Option<String>,
    pub api_key: Option<String>,
    pub model: Option<String>,
}

/// Check for existing configuration (Base URL, API Key, Model) from ~/.hermes/.env and config.yaml.
#[tauri::command]
pub async fn get_existing_config(hermes_home: Option<String>) -> Result<ExistingConfig, String> {
    let hermes_home_path = if let Some(home) = hermes_home {
        PathBuf::from(home)
    } else {
        crate::paths::hermes_home()
    };

    let mut result = ExistingConfig::default();

    // 1. Check .env
    let env_file = hermes_home_path.join(".env");
    if env_file.exists() {
        if let Ok(content) = std::fs::read_to_string(&env_file) {
            for line in content.lines() {
                let trimmed = line.trim();
                if trimmed.starts_with('#') || !trimmed.contains('=') {
                    continue;
                }
                let mut parts = trimmed.splitn(2, '=');
                let key = parts.next().unwrap_or("").trim();
                let mut val = parts.next().unwrap_or("").trim().to_string();
                if (val.starts_with('"') && val.ends_with('"')) || (val.starts_with('\'') && val.ends_with('\'')) {
                    if val.len() >= 2 {
                        val = val[1..val.len() - 1].to_string();
                    }
                }
                if (key == "IAMDS_LITELLM_API_KEY" || key == "OPENAI_API_KEY") && result.api_key.is_none() && !val.is_empty() {
                    result.api_key = Some(val);
                } else if (key == "IAMDS_LITELLM_BASE_URL" || key == "OPENAI_BASE_URL") && result.base_url.is_none() && !val.is_empty() {
                    result.base_url = Some(val);
                }
            }
        }
    }

    // 2. Check config.yaml
    let config_file = hermes_home_path.join("config.yaml");
    if config_file.exists() {
        if let Ok(content) = std::fs::read_to_string(&config_file) {
            for line in content.lines() {
                let trimmed = line.trim();
                if (trimmed.starts_with("default:") || trimmed.starts_with("model:")) && result.model.is_none() {
                    let mut parts = trimmed.splitn(2, ':');
                    parts.next();
                    let val = parts.next().unwrap_or("").trim().trim_matches('"').trim_matches('\'').to_string();
                    if !val.is_empty() && val != "{}" && !val.contains('\n') {
                        result.model = Some(val);
                    }
                } else if trimmed.starts_with("base_url:") && result.base_url.is_none() {
                    let mut parts = trimmed.splitn(2, ':');
                    parts.next();
                    let val = parts.next().unwrap_or("").trim().trim_matches('"').trim_matches('\'').to_string();
                    if !val.is_empty() {
                        result.base_url = Some(val);
                    }
                }
            }
        }
    }

    Ok(result)
}
