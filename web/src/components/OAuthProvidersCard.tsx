import { useEffect, useState, useCallback, useRef } from "react";
import {
  ShieldCheck,
  ShieldOff,
  ExternalLink,
  RefreshCw,
  Terminal,
  Settings2,
  Check,
} from "lucide-react";
import { api, type OAuthProvider } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { CopyButton } from "@nous-research/ui/ui/components/command-block";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { OAuthLoginModal } from "@/components/OAuthLoginModal";
import { useI18n } from "@/i18n";

interface Props {
  onError?: (msg: string) => void;
  onSuccess?: (msg: string) => void;
}

function formatExpiresAt(
  expiresAt: string | null | undefined,
  expiresInTemplate: string,
): string | null {
  if (!expiresAt) return null;
  try {
    const dt = new Date(expiresAt);
    if (Number.isNaN(dt.getTime())) return null;
    const now = Date.now();
    const diff = dt.getTime() - now;
    if (diff < 0) return "expired";
    const mins = Math.floor(diff / 60_000);
    if (mins < 60) return expiresInTemplate.replace("{time}", `${mins}m`);
    const hours = Math.floor(mins / 60);
    if (hours < 24) return expiresInTemplate.replace("{time}", `${hours}h`);
    const days = Math.floor(hours / 24);
    return expiresInTemplate.replace("{time}", `${days}d`);
  } catch {
    return null;
  }
}

export function OAuthProvidersCard({ onError, onSuccess }: Props) {
  const [providers, setProviders] = useState<OAuthProvider[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loginFor, setLoginFor] = useState<OAuthProvider | null>(null);
  const [disconnectTarget, setDisconnectTarget] =
    useState<OAuthProvider | null>(null);
  const [m365ConfigOpen, setM365ConfigOpen] = useState(false);
  const [m365ClientId, setM365ClientId] = useState("");
  const [m365TenantId, setM365TenantId] = useState("");
  const [savingM365, setSavingM365] = useState(false);
  const { t } = useI18n();

  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .getOAuthProviders()
      .then((resp) => setProviders(resp.providers))
      .catch((e) => onErrorRef.current?.(`Failed to load providers: ${e}`))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openM365Config = async () => {
    try {
      const vars = await api.getEnvVars();
      let cid = "";
      let tid = "";
      if (vars["M365_CLIENT_ID"]?.is_set) {
        cid = await api.revealEnvVar("M365_CLIENT_ID").then((r) => r.value).catch(() => "");
      }
      if (vars["M365_TENANT_ID"]?.is_set) {
        tid = await api.revealEnvVar("M365_TENANT_ID").then((r) => r.value).catch(() => "");
      }
      setM365ClientId(cid);
      setM365TenantId(tid);
    } catch {
      // ignore
    }
    setM365ConfigOpen(true);
  };

  const saveM365Config = async () => {
    setSavingM365(true);
    try {
      if (m365ClientId.trim()) {
        await api.setEnvVar("M365_CLIENT_ID", m365ClientId.trim());
      } else {
        await api.deleteEnvVar("M365_CLIENT_ID");
      }
      if (m365TenantId.trim()) {
        await api.setEnvVar("M365_TENANT_ID", m365TenantId.trim());
      } else {
        await api.deleteEnvVar("M365_TENANT_ID");
      }
      onSuccess?.("Microsoft 365 Client ID / Tenant ID settings updated");
      setM365ConfigOpen(false);
      refresh();
    } catch (e) {
      onError?.(`Failed to save M365 settings: ${e}`);
    } finally {
      setSavingM365(false);
    }
  };

  const handleDisconnect = async (provider: OAuthProvider) => {
    setBusyId(provider.id);
    setDisconnectTarget(null);
    try {
      await api.disconnectOAuthProvider(provider.id);
      onSuccess?.(`${provider.name} ${t.oauth.disconnect.toLowerCase()}ed`);
      refresh();
    } catch (e) {
      onError?.(`${t.oauth.disconnect} failed: ${e}`);
    } finally {
      setBusyId(null);
    }
  };

  const connectedCount =
    providers?.filter((p) => p.status.logged_in).length ?? 0;
  const totalCount = providers?.length ?? 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-base">
              {t.oauth.providerLogins}
            </CardTitle>
          </div>
          <Button
            ghost
            size="icon"
            className="text-muted-foreground hover:text-foreground"
            onClick={refresh}
            disabled={loading}
            aria-label={t.common.refresh}
          >
            {loading ? <Spinner /> : <RefreshCw />}
          </Button>
        </div>
        <CardDescription>
          {t.oauth.description
            .replace("{connected}", String(connectedCount))
            .replace("{total}", String(totalCount))}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && providers === null && (
          <div className="flex items-center justify-center py-8">
            <Spinner className="text-xl text-primary" />
          </div>
        )}
        {providers && providers.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">
            {t.oauth.noProviders}
          </p>
        )}
        <div className="flex flex-col divide-y divide-border">
          {providers?.map((p) => {
            const expiresLabel = formatExpiresAt(
              p.status.expires_at,
              t.oauth.expiresIn,
            );
            const isBusy = busyId === p.id;
            return (
              <div
                key={p.id}
                className="flex items-center justify-between gap-4 py-3"
              >
                <div className="flex items-start gap-3 min-w-0 flex-1">
                  {p.status.logged_in ? (
                    <ShieldCheck className="h-5 w-5 text-success shrink-0 mt-0.5" />
                  ) : (
                    <ShieldOff className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
                  )}
                  <div className="flex flex-col min-w-0 gap-0.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">{p.name}</span>
                      <Badge
                        tone="outline"
                        className="text-xs tracking-wide"
                      >
                        {t.oauth.flowLabels[p.flow]}
                      </Badge>
                      {p.status.logged_in && (
                        <Badge tone="success" className="text-xs">
                          {t.oauth.connected}
                        </Badge>
                      )}
                      {expiresLabel === "expired" && (
                        <Badge tone="destructive" className="text-xs">
                          {t.oauth.expired}
                        </Badge>
                      )}
                      {expiresLabel && expiresLabel !== "expired" && (
                        <Badge tone="outline" className="text-xs">
                          {expiresLabel}
                        </Badge>
                      )}
                    </div>
                    {p.status.logged_in && p.status.token_preview && (
                      <span className="truncate text-xs font-mono-ui text-text-secondary">
                        <span className="text-text-tertiary">token </span>
                        {p.status.token_preview}
                        {p.status.source_label && (
                          <span className="text-text-tertiary">
                            {" "}
                            · {p.status.source_label}
                          </span>
                        )}
                      </span>
                    )}
                    {!p.status.logged_in && (
                      <>
                        <span className="text-xs text-text-secondary">
                          {t.oauth.notConnected.split("{command}")[0].trimEnd()}
                          {t.oauth.notConnected.split("{command}")[1] ?? ""}
                        </span>

                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <code className="font-courier truncate text-xs opacity-60">
                            {p.cli_command}
                          </code>

                          <CopyButton
                            text={p.cli_command}
                            label={t.oauth.cli}
                            copiedLabel={t.oauth.copied}
                          />
                        </div>
                      </>
                    )}
                    {p.status.error && (
                      <span className="text-xs text-destructive">
                        {p.status.error}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  {p.id === "microsoft" && (
                    <Button
                      ghost
                      size="icon"
                      onClick={openM365Config}
                      title="Configure custom M365 Client ID & Tenant ID"
                    >
                      <Settings2 className="h-4 w-4" />
                    </Button>
                  )}
                  {p.docs_url && (
                    <a
                      href={p.docs_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex"
                      title={`Open ${p.name} docs`}
                    >
                      <Button ghost size="icon">
                        <ExternalLink />
                      </Button>
                    </a>
                  )}
                  {!p.status.logged_in && p.flow !== "external" && (
                    <Button
                      size="sm"
                      className="uppercase"
                      onClick={() => setLoginFor(p)}
                    >
                      {t.oauth.login}
                    </Button>
                  )}
                  {p.status.logged_in && p.flow !== "external" && (
                    <Button
                      size="sm"
                      outlined
                      className="uppercase"
                      onClick={() => setDisconnectTarget(p)}
                      disabled={isBusy}
                      prefix={isBusy ? <Spinner /> : undefined}
                    >
                      {t.oauth.disconnect}
                    </Button>
                  )}
                  {p.status.logged_in && p.flow === "external" && (
                    <span className="text-xs text-text-tertiary italic px-2">
                      <Terminal className="h-3 w-3 inline mr-0.5" />
                      {t.oauth.managedExternally}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
      {loginFor && (
        <OAuthLoginModal
          provider={loginFor}
          onClose={() => {
            setLoginFor(null);
            refresh();
          }}
          onSuccess={(msg) => onSuccess?.(msg)}
          onError={(msg) => onError?.(msg)}
        />
      )}
      <ConfirmDialog
        open={disconnectTarget !== null}
        onCancel={() => setDisconnectTarget(null)}
        onConfirm={() => {
          if (disconnectTarget) void handleDisconnect(disconnectTarget);
        }}
        title={`${t.oauth.disconnect} ${disconnectTarget?.name ?? ""}?`}
        description={`This will remove the stored OAuth tokens for ${disconnectTarget?.name ?? "this provider"}. You will need to re-authenticate to use it again.`}
        destructive
        confirmLabel={t.oauth.disconnect}
      />
      {m365ConfigOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 backdrop-blur-sm p-4"
          onClick={(e) => e.target === e.currentTarget && setM365ConfigOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className="relative w-full max-w-md border border-border bg-card p-6 shadow-2xl flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold">Microsoft 365 App Registration</h3>
              <Button ghost size="icon" onClick={() => setM365ConfigOpen(false)}>
                ✕
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Configure custom Azure AD Application (Client) ID and Tenant ID. Leave blank to use default multi-tenant settings.
            </p>
            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="m365-client-id">M365 Client ID</Label>
                <Input
                  id="m365-client-id"
                  value={m365ClientId}
                  onChange={(e) => setM365ClientId(e.target.value)}
                  placeholder="e.g. 41c29967-8ee6-4fac-b484-e87460272bda"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="m365-tenant-id">M365 Tenant ID</Label>
                <Input
                  id="m365-tenant-id"
                  value={m365TenantId}
                  onChange={(e) => setM365TenantId(e.target.value)}
                  placeholder="e.g. organizations or tenant UUID"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-2">
              <Button outlined onClick={() => setM365ConfigOpen(false)}>
                Cancel
              </Button>
              <Button onClick={saveM365Config} disabled={savingM365}>
                {savingM365 ? <Spinner /> : "Save"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
