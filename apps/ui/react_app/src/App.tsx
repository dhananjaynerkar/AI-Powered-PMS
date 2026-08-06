import {
  createContext,
  FormEvent,
  type MouseEvent,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  ApiError,
  createCase,
  createChat,
  endDemoSession,
  endLocalSession,
  loadAudit,
  loadCaseQueue,
  loadChat,
  loadChats,
  loadCaseRecipients,
  loadDemoMe,
  loadDemoStatus,
  loadDocument,
  loadLocalAuthStatus,
  loadMe,
  loadTimeline,
  loadRetrievalReadiness,
  loginLocally,
  postCaseMessage,
  updateChat,
  runPolicyQuery,
  runPolicyQueryStream,
  runStructuredQuery,
  startDemoSession,
  submitToHod,
  submitToNo,
  uploadDocument,
  verifyCase
} from "./api";
import type {
  AuditEvent,
  CaseRecord,
  CaseTimeline,
  ChatResponse,
  ChatSummary,
  DemoAnswer,
  DemoIdentity,
  DocumentMetadata,
  DocumentUploadResult,
  GroundedAnswer,
  LocalLoginRole,
  Me,
  RuntimeHealth,
  RetrievalReadiness,
  StaffRecipient,
  StructuredAnswer
} from "./types";
import "./styles.css";
import { loadRuntimeHealth } from "./api/client";

type IconName =
  | "anchor"
  | "bar"
  | "bell"
  | "bot"
  | "building"
  | "calendar"
  | "database"
  | "file"
  | "grid"
  | "home"
  | "help"
  | "lock"
  | "logout"
  | "map"
  | "message"
  | "refresh"
  | "rupee"
  | "search"
  | "send"
  | "shield"
  | "upload"
  | "users";

type PageState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "unauthorized"; message: string };

interface RouterState {
  pathname: string;
  search: string;
  navigate: (to: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterState | null>(null);

function useRouter(): RouterState {
  const context = useContext(RouterContext);
  if (context === null) throw new Error("router context is missing");
  return context;
}

function BrowserRouter({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState(() => ({
    pathname: window.location.pathname,
    search: window.location.search
  }));

  useEffect(() => {
    const update = () => setLocation({ pathname: window.location.pathname, search: window.location.search });
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);

  function navigate(to: string, options: { replace?: boolean } = {}) {
    const target = new URL(to, window.location.origin);
    if (options.replace) window.history.replaceState({}, "", target);
    else window.history.pushState({}, "", target);
    setLocation({ pathname: target.pathname, search: target.search });
  }

  return <RouterContext.Provider value={{ ...location, navigate }}>{children}</RouterContext.Provider>;
}

function Link({
  to,
  children,
  className,
  title,
  "aria-label": ariaLabel
}: {
  to: string;
  children: ReactNode;
  className?: string;
  title?: string;
  "aria-label"?: string;
}) {
  const { navigate } = useRouter();
  function click(event: MouseEvent<HTMLAnchorElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  }
  return <a aria-label={ariaLabel} className={className} href={to} onClick={click} title={title}>{children}</a>;
}

function matchRoute(pattern: string, pathname: string): Record<string, string> | null {
  const patternParts = pattern.split("/").filter(Boolean);
  const pathParts = pathname.split("/").filter(Boolean);
  if (patternParts.length !== pathParts.length) return null;
  const params: Record<string, string> = {};
  for (let index = 0; index < patternParts.length; index += 1) {
    const patternPart = patternParts[index];
    const pathPart = pathParts[index];
    if (patternPart.startsWith(":")) params[patternPart.slice(1)] = decodeURIComponent(pathPart);
    else if (patternPart !== pathPart) return null;
  }
  return params;
}

const ALL_STAFF_ROLES = ["Data Entry Operator", "Nodal/Regional Officer", "HOD"] as const;
const REVIEW_ROLES = ["Nodal/Regional Officer", "HOD"] as const;
const HOD_ONLY = ["HOD"] as const;

const NAV_ITEMS: Array<{
  label: string;
  path: string;
  icon: IconName;
  roles: readonly string[];
}> = [
  { label: "Home", path: "/", icon: "home", roles: ["Tenant", ...ALL_STAFF_ROLES] },
  { label: "Dashboard", path: "/dashboard", icon: "grid", roles: ["Tenant", ...ALL_STAFF_ROLES] },
  { label: "Tenants", path: "/tenants", icon: "users", roles: ALL_STAFF_ROLES },
  { label: "Lease Management", path: "/leases", icon: "file", roles: ["Tenant", ...ALL_STAFF_ROLES] },
  { label: "Land Monitoring", path: "/land", icon: "map", roles: ALL_STAFF_ROLES },
  { label: "Policy Repository", path: "/policies", icon: "database", roles: ["Tenant", ...ALL_STAFF_ROLES] },
  { label: "Document Upload", path: "/documents", icon: "upload", roles: ALL_STAFF_ROLES },
  { label: "AI Assistant", path: "/assistant", icon: "bot", roles: ["Tenant", ...ALL_STAFF_ROLES] },
  { label: "Analytics", path: "/analytics", icon: "bar", roles: ALL_STAFF_ROLES },
  { label: "Audit Logs", path: "/audit", icon: "file", roles: REVIEW_ROLES },
  { label: "Approval Queue", path: "/approvals", icon: "shield", roles: REVIEW_ROLES },
  { label: "Forecasting", path: "/forecasting", icon: "bar", roles: HOD_ONLY },
  { label: "Legal Cases", path: "/legal-cases", icon: "building", roles: REVIEW_ROLES }
];

const iconPaths: Record<IconName, ReactNode> = {
  anchor: <><circle cx="12" cy="5" r="2" /><path d="M12 7v12" /><path d="M5 12H3c0 4.5 3.5 8 9 8s9-3.5 9-8h-2" /><path d="M8 12h8" /></>,
  bar: <><path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" /></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></>,
  bot: <><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 4v4" /><path d="M8 13h.01" /><path d="M16 13h.01" /><path d="M9 17h6" /></>,
  building: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M9 21v-4h6v4" /><path d="M8 7h.01" /><path d="M12 7h.01" /><path d="M16 7h.01" /><path d="M8 11h.01" /><path d="M12 11h.01" /><path d="M16 11h.01" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4" /><path d="M8 2v4" /><path d="M3 10h18" /></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8" /><path d="M8 17h6" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /></>,
  home: <><path d="m3 11 9-8 9 8" /><path d="M5 10v10h14V10" /><path d="M9 20v-6h6v6" /></>,
  help: <><circle cx="12" cy="12" r="10" /><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.2-3 4" /><path d="M12 17h.01" /></>,
  lock: <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>,
  map: <><path d="M9 18l-6 3V6l6-3 6 3 6-3v15l-6 3-6-3z" /><path d="M9 3v15" /><path d="M15 6v15" /></>,
  message: <><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" /></>,
  refresh: <><path d="M21 12a9 9 0 0 1-15.5 6.2" /><path d="M3 12A9 9 0 0 1 18.5 5.8" /><path d="M18 2v4h4" /><path d="M6 22v-4H2" /></>,
  rupee: <><path d="M6 3h12" /><path d="M6 8h12" /><path d="M6 13h5a5 5 0 0 0 0-10" /><path d="M6 13l8 8" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>,
  send: <><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></>,
  shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></>,
  upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M17 8l-5-5-5 5" /><path d="M12 3v12" /></>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>
};

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg aria-hidden className="icon" height={size} viewBox="0 0 24 24" width={size}>{iconPaths[name]}</svg>;
}

function errorMessage(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === "string") return reason;
  if (reason && typeof reason === "object") {
    const detail = (reason as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    try {
      return JSON.stringify(reason);
    } catch {
      return "Request failed";
    }
  }
  return "Request failed";
}

const ACTIVITY_STAGE_LABELS: Record<string, string> = {
  reading_question: "Reading your question…",
  retrieving_authorized_evidence: "Searching authorized records…",
  searching_authorized_records: "Searching authorized records…",
  searching_document_evidence: "Searching document evidence…",
  reranking_evidence: "Reranking evidence…",
  generating_answer: "Generating answer…",
  validating_citations: "Validating citations…",
  saving_response: "Saving response…"
};

function activityStageLabel(stage: string | null): string {
  if (!stage) return ACTIVITY_STAGE_LABELS.reading_question;
  return ACTIVITY_STAGE_LABELS[stage.trim().toLowerCase()] ?? ACTIVITY_STAGE_LABELS.reading_question;
}

function AssistantActivity({ stage, nearComposer = false }: { stage: string | null; nearComposer?: boolean }) {
  return (
    <div className={`assistant-activity${nearComposer ? " composer-activity" : ""}`} aria-live="polite" role="status">
      <span className="activity-spinner" aria-hidden="true" />
      <span>{activityStageLabel(stage)}</span>
    </div>
  );
}

function isDenied(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 403;
}

function hasAnyRole(me: Me, roles: readonly string[]): boolean {
  return me.roles.some((role) => roles.includes(role));
}

function isTenant(me: Me): boolean {
  return me.roles.includes("Tenant");
}

function StatusBadge({ children, tone = "ready" }: { children: string; tone?: "ready" | "pending" | "danger" | "neutral" }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function AppBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="app-brand">
      <div className="brand-mark" aria-hidden><Icon name="anchor" size={18} /></div>
      <div>
        <p className="brand-overline">Indian Port Authority</p>
        <strong>AI Powered Port Management System</strong>
      </div>
    </div>
  );
}

function LoadingSkeleton({ label = "Loading data from the configured source." }: { label?: string }) {
  return <div className="state-card loading-state" aria-live="polite"><span /><span /><span /><p>{label}</p></div>;
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="state-card"><Icon name="file" /><h2>{title}</h2><p>{text}</p></div>;
}

function ErrorState({ message }: { message: string }) {
  return <div className="state-card error-state" role="alert"><Icon name="help" /><h2>Request failed</h2><p>{message}</p></div>;
}

function PermissionDenied() {
  return <div className="state-card denied-state" role="alert"><Icon name="lock" /><h1>403</h1><h2>Permission denied</h2><p>Your server-issued role is not authorized for this page.</p><Link to="/dashboard">Return to dashboard</Link></div>;
}

function SearchInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="search-input">
      <span>{label}</span>
      <div><Icon name="search" size={17} /><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={label} /></div>
    </label>
  );
}

function FilterBar({ children }: { children: ReactNode }) {
  return <section className="filter-bar" aria-label="Page filters">{children}</section>;
}

function PageHeader({ title, subtitle, children }: { title: string; subtitle: string; children?: ReactNode }) {
  const { pathname } = useRouter();
  const segments = pathname.split("/").filter(Boolean);
  return (
    <header className="page-header">
      <nav aria-label="Breadcrumbs" className="breadcrumbs">
        <Link to="/dashboard">Dashboard</Link>
        {segments.map((segment, index) => {
          const path = `/${segments.slice(0, index + 1).join("/")}`;
          return <Link key={path} to={path}>{segment.replaceAll("-", " ")}</Link>;
        })}
      </nav>
      <div className="page-title-row">
        <div><h1>{title}</h1><p>{subtitle}</p></div>
        {children}
      </div>
    </header>
  );
}

function DataTable({
  columns,
  rows,
  getRowPath
}: {
  columns: Array<{ key: string; label: string }>;
  rows: Array<Record<string, ReactNode>>;
  getRowPath?: (row: Record<string, ReactNode>) => string;
}) {
  if (rows.length === 0) return <EmptyState title="No records found" text="Data not available from the configured source." />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => <td key={column.key}>{getRowPath && column.key === columns[0].key ? <Link to={getRowPath(row)}>{row[column.key]}</Link> : row[column.key]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfirmDialog({
  title,
  text,
  confirmLabel,
  onCancel,
  onConfirm
}: {
  title: string;
  text: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="dialog-backdrop" role="presentation">
      <section aria-modal="true" className="confirm-dialog" role="dialog">
        <h2>{title}</h2>
        <p>{text}</p>
        <div><button className="secondary-action" onClick={onCancel} type="button">Cancel</button><button onClick={onConfirm} type="button">{confirmLabel}</button></div>
      </section>
    </div>
  );
}

function PublicLogin({
  error,
  demoAvailable,
  localAuthAvailable,
  startDemo,
  startLocalLogin
}: {
  error: string | null;
  demoAvailable: boolean;
  localAuthAvailable: boolean;
  startDemo: (identity: DemoIdentity) => void;
  startLocalLogin: (username: string, password: string, role: LocalLoginRole) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [role, setRole] = useState<LocalLoginRole>("Data Entry Operator");
  const { navigate } = useRouter();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) return;
    startLocalLogin(username.trim(), password, role);
  }

  return (
    <main className="public-portal">
      <div className="public-topbar"><span>भारत सरकार | Government of India</span><span>Ministry of Ports, Shipping & Waterways</span><span className="language-pill">English</span></div>
      <header className="public-header"><AppBrand /><nav aria-label="Public navigation"><button className="active" type="button">Home</button><button type="button">Policies</button><button type="button">Help</button></nav></header>
      <section className="public-hero">
        <div>
          <p className="hero-label">Local enterprise prototype</p>
          <h1>AI Powered Port Management System</h1>
          <p>Role-aware estate workflow, governed PostgreSQL access, document evidence, and audit-ready collaboration for port land administration.</p>
          {demoAvailable && (
            <div className="portal-actions">
              <button className="gold-action" onClick={() => { startDemo("demo.do"); navigate("/assistant"); }} type="button">Try as Data Entry Operator</button>
              <button className="outline-action" onClick={() => { startDemo("demo.no"); navigate("/approvals"); }} type="button">Try as Nodal Officer</button>
              <button className="outline-action" onClick={() => { startDemo("demo.hod"); navigate("/dashboard"); }} type="button">Try as HOD</button>
            </div>
          )}
          {error && <div className="public-alert" role="alert">{error}</div>}
        </div>
      </section>
      <section className="portal-login staff">
        <div className="login-card">
          <div className="login-heading"><span className="square-icon"><Icon name="lock" /></span><div><h1>Secure Login</h1><p>Use the configured local account for Tenant, Data Entry Operator, Nodal/Regional Officer or HOD.</p></div></div>
          <form className="login-form" onSubmit={submit}>
            <label>Role<select value={role} onChange={(event) => setRole(event.target.value as LocalLoginRole)}><option>Data Entry Operator</option><option>Nodal/Regional Officer</option><option>HOD</option><option>Tenant</option></select></label>
            <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
            <label>Password<div className="password-control"><input autoComplete="current-password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} /><button aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} className="password-toggle" onClick={() => setShowPassword((visible) => !visible)} type="button">{showPassword ? "Hide" : "Show"}</button></div></label>
            <button className="primary-action" disabled={!localAuthAvailable} type="submit"><Icon name="lock" size={17} /> Sign in</button>
          </form>
          <p className="login-note">Use the exact <code>demo_password</code> from this account row. The PostgreSQL administrator password is not an application login password.</p>
          {!localAuthAvailable && <p className="login-note">Local password authentication is not enabled in the configured backend.</p>}
        </div>
      </section>
    </main>
  );
}

function Sidebar({
  me,
  demoActive,
  exitDemo,
  returnHome
}: {
  me: Me;
  demoActive: boolean;
  exitDemo: () => void;
  returnHome: () => void;
}) {
  const { pathname } = useRouter();
  const visibleItems = NAV_ITEMS.filter((item) => hasAnyRole(me, item.roles));
  return (
    <aside className="app-sidebar">
      <AppBrand compact />
      <nav aria-label="Application navigation">
        {visibleItems.map((item) => (
          item.path === "/"
            ? (
              <button aria-label={item.label} key={item.path} onClick={returnHome} title={item.label} type="button">
                <Icon name={item.icon} size={18} />
                <span>{item.label}</span>
              </button>
            )
            : (
              <Link aria-label={item.label} className={pathname.startsWith(item.path) ? "active" : ""} key={item.path} title={item.label} to={item.path}>
                <Icon name={item.icon} size={18} />
                <span>{item.label}</span>
              </Link>
            )
        ))}
      </nav>
      <button className="sidebar-logout" onClick={demoActive ? exitDemo : returnHome} type="button"><Icon name="logout" size={18} /> Logout</button>
    </aside>
  );
}

function TopBar({ me, demoActive }: { me: Me; demoActive: boolean }) {
  const initials = me.subject.split(".").map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "U";
  return (
    <header className="app-topbar">
      <AppBrand compact />
      <div className="topbar-actions">
        {demoActive && <StatusBadge tone="pending">Controlled local demo</StatusBadge>}
        <StatusBadge>Secure session</StatusBadge>
        <button className="icon-button" aria-label="Notifications" type="button"><Icon name="bell" size={19} /></button>
        <span className="avatar">{initials}</span>
        <div className="user-block"><strong>{me.subject}</strong><span>{me.roles.join(", ")}</span></div>
      </div>
    </header>
  );
}

function AppShell({
  me,
  demoActive,
  exitDemo,
  returnHome,
  children
}: {
  me: Me;
  demoActive: boolean;
  exitDemo: () => void;
  returnHome: () => void;
  children: ReactNode;
}) {
  return (
    <main className="dashboard-shell">
      <Sidebar me={me} demoActive={demoActive} exitDemo={exitDemo} returnHome={returnHome} />
      <section className="dashboard-main">
        <TopBar me={me} demoActive={demoActive} />
        {children}
      </section>
    </main>
  );
}

function ProtectedRoute({ me, allowed, children }: { me: Me; allowed: readonly string[]; children: ReactNode }) {
  if (!hasAnyRole(me, allowed)) return <PermissionDenied />;
  return <>{children}</>;
}

function DashboardPage({ me, cases, audit, demoActive }: { me: Me; cases: CaseRecord[]; audit: AuditEvent[]; demoActive: boolean }) {
  const role = me.roles[0] ?? "User";
  const draftCases = cases.filter((item) => item.state === "draft").length;
  const returnedCases = cases.filter((item) => item.state.includes("returned")).length;
  const pendingCases = cases.filter((item) => item.state.includes("submitted")).length;
  return (
    <>
      <PageHeader title={`${role} Dashboard`} subtitle="Server-authorized estate workflow overview">
        {demoActive && <StatusBadge tone="pending">Demo data boundary visible</StatusBadge>}
      </PageHeader>
      <section className="metric-row">
        <StatCard icon="message" label="Assigned cases" value={String(cases.length)} source="GET /api/v1/cases" />
        <StatCard icon="file" label="Draft cases" value={String(draftCases)} source="Derived from authorized cases" />
        <StatCard icon="refresh" label="Returned for correction" value={String(returnedCases)} source="Derived from authorized cases" />
        <StatCard icon="shield" label="Pending submission/review" value={String(pendingCases)} source="Derived from authorized cases" />
      </section>
      <section className="dashboard-grid">
        <Panel title="My Work Queue" icon="message"><CaseTable cases={cases.slice(0, 8)} /></Panel>
        <Panel title="Document Processing Status" icon="upload"><Unavailable capability="Document ingestion rollup endpoint" /></Panel>
        <Panel title="Authorized Estate/Plot Summary" icon="map"><Unavailable capability="Estate aggregation endpoint" /></Panel>
        <Panel title="Recent Assistant Activity" icon="bot"><AuditList audit={audit.slice(0, 5)} /></Panel>
      </section>
    </>
  );
}

function StatCard({ label, value, source, icon }: { label: string; value: string; source: string; icon: IconName }) {
  return <article className="metric-card"><div><p>{label}</p><strong>{value}</strong><span>{source}</span></div><span><Icon name={icon} /></span></article>;
}

function Panel({ title, icon, children }: { title: string; icon: IconName; children: ReactNode }) {
  return <section className="panel"><div className="panel-title"><span><Icon name={icon} /></span><div><h2>{title}</h2><p>Server data only. Missing sources are explicitly marked.</p></div></div>{children}</section>;
}

function Unavailable({ capability }: { capability: string }) {
  return <EmptyState title="Data not available from the configured source." text={`${capability} is not exposed by the current backend API.`} />;
}

function CaseTable({ cases }: { cases: CaseRecord[] }) {
  return (
    <DataTable
      columns={[
        { key: "title", label: "Case" },
        { key: "state", label: "State" },
        { key: "owner", label: "Owner" },
        { key: "updated", label: "Updated" }
      ]}
      rows={cases.map((item) => ({
        title: item.title,
        state: item.state.replaceAll("_", " "),
        owner: item.current_owner_role,
        updated: new Date(item.updated_at).toLocaleString(),
        id: item.case_id
      }))}
      getRowPath={(row) => `/assistant/cases/${String(row.id)}`}
    />
  );
}

function AuditList({ audit }: { audit: AuditEvent[] }) {
  if (audit.length === 0) return <EmptyState title="No visible audit events" text="No audit rows were returned for this role." />;
  return <ul className="audit-list">{audit.map((item) => <li key={item.event_id}><strong>{item.result_status}</strong><span>{item.query_category}</span><small>{new Date(item.occurred_at).toLocaleString()} - {item.correlation_id}</small></li>)}</ul>;
}

function RegistryPage({ module }: { module: "tenants" | "leases" | "land" }) {
  const { pathname, search: routeSearch, navigate } = useRouter();
  const params = useMemo(() => new URLSearchParams(routeSearch), [routeSearch]);
  const search = params.get("search") ?? "";
  const status = params.get("status") ?? "all";
  const titles = {
    tenants: ["Tenant Registry", "Tenant list requires an approved tenant registry endpoint before records can be displayed."],
    leases: ["Lease Management", "Lease data can be queried through approved structured routes; list/detail APIs are not yet exposed."],
    land: ["Land Monitoring", "Plot and estate records need a governed plot-register endpoint or semantic view."],
  } as const;
  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    navigate(`${pathname}?${next.toString()}`);
  }
  return (
    <>
      <PageHeader title={titles[module][0]} subtitle={titles[module][1]} />
      <FilterBar>
        <SearchInput label={`Search ${module}`} value={search} onChange={(value) => update("search", value)} />
        <label>Status<select value={status} onChange={(event) => update("status", event.target.value)}><option value="all">All statuses</option><option value="active">Active</option><option value="expired">Expired</option><option value="review">Review required</option></select></label>
      </FilterBar>
      <Panel title={titles[module][0]} icon={module === "land" ? "map" : module === "tenants" ? "users" : "file"}>
        <Unavailable capability={`${titles[module][0]} list endpoint`} />
      </Panel>
      {module !== "tenants" && <StructuredWorkbench defaultQuestion={module === "land" ? "List available estates in the approved extract." : "Show five approved lease summaries."} />}
    </>
  );
}

function DetailPage({ kind, id }: { kind: "tenant" | "lease" | "plot" | "legal"; id: string }) {
  return (
    <>
      <PageHeader title={`${kind[0].toUpperCase()}${kind.slice(1)} Detail`} subtitle={`Requested record: ${id}`} />
      <Panel title="Record detail" icon="file">
        <Unavailable capability={`${kind} detail endpoint for ${id}`} />
      </Panel>
    </>
  );
}

function StructuredWorkbench({ defaultQuestion }: { defaultQuestion: string }) {
  const [question, setQuestion] = useState(defaultQuestion);
  const [state, setState] = useState<PageState<StructuredAnswer>>({ status: "idle" });
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setState({ status: "loading" });
    try {
      const answer = await runStructuredQuery(question.trim(), "");
      setState(answer.records.length === 0 ? { status: "empty" } : { status: "success", data: answer });
    } catch (reason) {
      setState(isDenied(reason) ? { status: "unauthorized", message: "Server denied the structured query." } : { status: "error", message: errorMessage(reason) });
    }
  }
  return (
    <Panel title="Approved Structured Query" icon="database">
      <form className="chat-form" onSubmit={submit}>
        <textarea aria-label="Structured question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={2} />
        <button type="submit"><Icon name="send" size={18} /> Run</button>
      </form>
      <AsyncState state={state} render={(answer) => <AnswerRecords answer={answer} />} />
    </Panel>
  );
}

function AsyncState<T>({ state, render }: { state: PageState<T>; render: (data: T) => ReactNode }) {
  if (state.status === "idle") return null;
  if (state.status === "loading") return <LoadingSkeleton />;
  if (state.status === "empty") return <EmptyState title="No matching records" text="The configured source returned no rows." />;
  if (state.status === "error") return <ErrorState message={state.message} />;
  if (state.status === "unauthorized") return <PermissionDenied />;
  return <>{render(state.data)}</>;
}

function AnswerRecords({ answer }: { answer: StructuredAnswer }) {
  return (
    <section className="answer-panel">
      <p>{answer.answer}</p>
      <DataTable
        columns={[
          { key: "facts", label: "Facts" },
          { key: "source", label: "Source" },
          { key: "freshness", label: "Freshness" }
        ]}
        rows={answer.records.map((record) => ({
          facts: Object.entries(record.values).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value ?? "not recorded"}`).join("; "),
          source: `${record.provenance.source_schema}.${record.provenance.source_table}`,
          freshness: record.provenance.freshness_at ? new Date(record.provenance.freshness_at).toLocaleString() : "not recorded"
        }))}
      />
    </section>
  );
}

function PolicyPage() {
  const [question, setQuestion] = useState("Explain an applicable land-management provision.");
  const [answer, setAnswer] = useState<PageState<GroundedAnswer>>({ status: "idle" });
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setAnswer({ status: "loading" });
    try {
      const result = await runPolicyQuery(question.trim(), "");
      setAnswer(result.sources.length === 0 && result.review_required ? { status: "empty" } : { status: "success", data: result });
    } catch (reason) {
      setAnswer(isDenied(reason) ? { status: "unauthorized", message: "Server denied policy retrieval." } : { status: "error", message: errorMessage(reason) });
    }
  }
  return (
    <>
      <PageHeader title="Policy Repository" subtitle="Governed document retrieval with page-level citation when available." />
      <FilterBar>
        <SearchInput label="Search policies" value={question} onChange={setQuestion} />
        <label>Category<select><option>All approved categories</option><option>Acts</option><option>Rules</option><option>Land-management policy</option><option>Circulars</option></select></label>
        <label>Language<select><option>All languages</option><option>English</option><option>Hindi</option><option>Marathi</option></select></label>
      </FilterBar>
      <Panel title="Policy AI Retrieval" icon="bot">
        <form className="chat-form" onSubmit={submit}><textarea aria-label="Policy question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={2} /><button type="submit"><Icon name="send" size={18} /> Retrieve</button></form>
        <AsyncState state={answer} render={(result) => <EvidencePanel answer={result} />} />
      </Panel>
    </>
  );
}

function PolicyDetailPage({ documentId }: { documentId: string }) {
  return (
    <>
      <PageHeader title="Policy Detail" subtitle={`Document ID: ${documentId}`} />
      <DocumentDetail documentId={documentId} />
    </>
  );
}

function EvidencePanel({ answer }: { answer: GroundedAnswer }) {
  return (
    <section className="evidence-panel">
      <p>{answer.answer}</p>
      <div className="answer-meta"><StatusBadge tone={answer.review_required ? "pending" : "ready"}>{answer.review_required ? "Review required" : "Grounded"}</StatusBadge><span>{answer.confidence}</span></div>
      <DataTable
        columns={[
          { key: "document", label: "Document" },
          { key: "version", label: "Version" },
          { key: "pages", label: "Pages" },
          { key: "clause", label: "Clause" }
        ]}
        rows={answer.sources.map((source) => ({
          document: source.document_title,
          version: source.document_version_id,
          pages: source.page_numbers.join(", "),
          clause: source.clause_number ?? source.section_number ?? "not recorded",
          id: source.document_id
        }))}
        getRowPath={(row) => `/policies/${String(row.id)}`}
      />
    </section>
  );
}

function documentAnswerForWorkspace(answer: GroundedAnswer, me: Me): DemoAnswer {
  return {
    answer: answer.answer,
    route: "DOCUMENT_RAG",
    principal: {
      username: me.subject,
      role: me.roles.join(", "),
      department: me.department_id ?? "not recorded",
      unit_id: me.unit_id ?? "not recorded",
      classification: me.classification
    },
    structured: null,
    document: answer,
    warnings: answer.warnings,
    review_required: answer.review_required,
    correlation_id: "",
    duration_ms: 0,
    evidence_extracted: false
  };
}

function DocumentsPage({ accessToken }: { accessToken: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [classification, setClassification] = useState("internal");
  const [state, setState] = useState<PageState<DocumentUploadResult>>({ status: "idle" });
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !title.trim()) return;
    setState({ status: "loading" });
    try {
      setState({ status: "success", data: await uploadDocument({ file, title: title.trim(), classification }, accessToken) });
    } catch (reason) {
      setState(isDenied(reason) ? { status: "unauthorized", message: "Server denied document upload." } : { status: "error", message: errorMessage(reason) });
    }
  }
  return (
    <>
      <PageHeader title="Document Upload" subtitle="Existing ingestion endpoint with validation, storage and review-required gates." />
      <Panel title="Upload document" icon="upload">
        <form className="upload-form" onSubmit={submit}>
          <label>File<input onChange={(event) => setFile(event.target.files?.[0] ?? null)} type="file" /></label>
          <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label>Classification<select value={classification} onChange={(event) => setClassification(event.target.value)}><option value="internal">Internal</option><option value="public">Public</option><option value="confidential">Confidential</option></select></label>
          <button type="submit"><Icon name="upload" size={18} /> Upload</button>
        </form>
        <AsyncState state={state} render={(result) => <DocumentStatus result={result} />} />
      </Panel>
      <Panel title="Pipeline status" icon="refresh"><Unavailable capability="Document registry list and ingestion-stage timeline endpoint" /></Panel>
    </>
  );
}

function DocumentStatus({ result }: { result: DocumentUploadResult }) {
  const doc = result.document;
  return (
    <DataTable
      columns={[{ key: "field", label: "Field" }, { key: "value", label: "Value" }]}
      rows={[
        { field: "Document ID", value: <Link to={`/documents/${doc.canonical_document_id}`}>{doc.canonical_document_id}</Link> },
        { field: "Status", value: doc.status },
        { field: "Duplicate", value: result.duplicate ? "Yes" : "No" },
        { field: "Checksum", value: doc.checksum_sha256 },
        { field: "Size", value: `${doc.size_bytes} bytes` }
      ]}
    />
  );
}

function DocumentDetailPage({ accessToken, documentId }: { accessToken: string; documentId: string }) {
  return (
    <>
      <PageHeader title="Document Detail" subtitle={`Document ID: ${documentId}`} />
      <DocumentDetail documentId={documentId} accessToken={accessToken} />
    </>
  );
}

function DocumentDetail({ documentId, accessToken = "" }: { documentId: string; accessToken?: string }) {
  const [state, setState] = useState<PageState<DocumentMetadata>>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const doc = await loadDocument(documentId, accessToken);
        if (!cancelled) setState({ status: "success", data: doc });
      } catch (reason) {
        if (!cancelled) setState(isDenied(reason) ? { status: "unauthorized", message: "Server denied document metadata." } : { status: "error", message: errorMessage(reason) });
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [accessToken, documentId]);
  return <Panel title="Document metadata" icon="file"><AsyncState state={state} render={(doc) => <DocumentMetadataTable doc={doc} />} /></Panel>;
}

function DocumentMetadataTable({ doc }: { doc: DocumentMetadata }) {
  return <DataTable columns={[{ key: "field", label: "Field" }, { key: "value", label: "Value" }]} rows={[
    { field: "Title", value: doc.title },
    { field: "Status", value: doc.status },
    { field: "Version", value: String(doc.version_number) },
    { field: "Classification", value: doc.classification },
    { field: "Checksum", value: doc.checksum_sha256 },
    { field: "Created", value: new Date(doc.created_at).toLocaleString() }
  ]} />;
}

function AssistantPage({
  accessToken,
  me,
  cases,
  chats,
  selectedChat,
  selectedChatId,
  timeline,
  demoActive,
  demoAnswer,
  busy,
  streamingStage,
  requestError,
  completionMessage,
  caseMessage,
  demoCaseMessage,
  selectCase,
  selectChat,
  createNewChat,
  renameChat,
  stopAssistantRequest,
  retryLastQuestion,
  createDemoCase,
  sendMessage,
  sendDemoCaseMessage,
  handoffToNo,
  verifyByNo,
  forwardToHod,
  submitDemo,
  setDemoQuestion,
  demoQuestion,
  setCaseMessage,
  setDemoCaseMessage,
  selectedCaseId
}: AssistantProps) {
  const { navigate } = useRouter();
  const [search, setSearch] = useState("");
  const [chatSearch, setChatSearch] = useState("");
  const [runtime, setRuntime] = useState<RuntimeHealth | null>(null);
  const [retrievalReadiness, setRetrievalReadiness] = useState<RetrievalReadiness | null>(null);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);
  useEffect(() => {
    let cancelled = false;
    async function refreshRuntime() {
      try {
        const health = await loadRuntimeHealth<RuntimeHealth>();
        const readiness = await loadRetrievalReadiness(accessToken);
        if (!cancelled) {
          setRuntime(health);
          setRetrievalReadiness(readiness);
          setRuntimeUnavailable(false);
        }
      } catch {
        if (!cancelled) setRuntimeUnavailable(true);
      }
    }
    void refreshRuntime();
    const timer = window.setInterval(() => void refreshRuntime(), 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [accessToken]);
  useEffect(() => {
    if (selectedCaseId && timeline?.case.case_id !== selectedCaseId) void selectCase(selectedCaseId);
  }, [selectedCaseId, selectCase, timeline?.case.case_id]);
  useEffect(() => {
    if (selectedChatId && selectedChat?.chat_id !== selectedChatId) void selectChat(selectedChatId);
  }, [selectedChat, selectedChatId, selectChat]);
  const visibleCases = cases.filter((item) => item.title.toLowerCase().includes(search.toLowerCase()) || item.case_id.toLowerCase().includes(search.toLowerCase()));
  const visibleChats = chats.filter((item) => item.title.toLowerCase().includes(chatSearch.toLowerCase()));
  return (
    <>
      <PageHeader title="AI Assistant" subtitle="Large chat workspace with case context and evidence drawer." />
      <section className="assistant-layout">
        <aside className="assistant-left">
          <button className="full-button" onClick={createNewChat} type="button"><Icon name="message" size={18} /> + New Chat</button>
          <SearchInput label="Search chats" value={chatSearch} onChange={setChatSearch} />
          <div className="case-list" aria-label="Saved chats">
            {visibleChats.length === 0 ? <EmptyState title="No saved chats" text="Create a chat to persist your conversation." /> : visibleChats.map((item) => (
              <button className={item.chat_id === selectedChatId ? "case-card active" : "case-card"} key={item.chat_id} onClick={() => selectChat(item.chat_id)} type="button">
                <strong>{item.title}</strong><span>{item.chat_type === "SHARED_CASE" ? "Shared" : "Personal"}</span><small>{new Date(item.updated_at).toLocaleString()}</small>
              </button>
            ))}
          </div>
          {selectedChat && <button className="secondary-button" type="button" onClick={() => {
            const title = window.prompt("Chat title", selectedChat.title)?.trim();
            if (title) void renameChat(selectedChat.chat_id, title);
          }}>Rename selected chat</button>}
          {!isTenant(me) && <button className="full-button" onClick={createDemoCase} type="button"><Icon name="message" size={18} /> New Case</button>}
          <SearchInput label="Search cases" value={search} onChange={setSearch} />
          <div className="case-list">
            {visibleCases.length === 0 ? <EmptyState title="No cases visible" text="No authorized cases match the current filter." /> : visibleCases.map((item) => (
              <button className={item.case_id === selectedCaseId ? "case-card active" : "case-card"} key={item.case_id} onClick={() => navigate(`/assistant/cases/${item.case_id}`)} type="button">
                <strong>{item.title}</strong><span>{item.state.replaceAll("_", " ")}</span><small>{item.case_id}</small>
              </button>
            ))}
          </div>
        </aside>
        <section className="assistant-center">
          <div className="assistant-header"><StatusBadge>{runtimeUnavailable ? "Backend unavailable" : retrievalReadiness?.ready_for_questions ? "Ready" : "Not ready"}</StatusBadge><span>{retrievalReadiness ? `${retrievalReadiness.indexed_documents} indexed documents · ${retrievalReadiness.embedded_child_chunks} embedded chunks · ${retrievalReadiness.generation_model} ${retrievalReadiness.generation_model_state}` : runtime ? `Backend ${runtime.runtime_id} is checking retrieval readiness.` : "Connecting to the configured backend."}</span></div>
          <div className="chat-thread" aria-live="polite">
            {!timeline && !selectedChat && <EmptyState title="Open or create a case" text="The assistant can answer controlled questions without a case, but handoff evidence is attached only when a case is open." />}
            {selectedChat?.messages.map((message) => <article className="assistant-bubble" key={message.message_id}><strong>{message.message_role}</strong><p>{message.content}</p><small>{new Date(message.created_at).toLocaleString()}</small></article>)}
            {timeline?.messages.map((message) => <article className="assistant-bubble" key={message.message_id}><strong>{message.author_role}</strong><p>{message.body}</p><small>{new Date(message.created_at).toLocaleString()}</small></article>)}
            {busy && <AssistantActivity stage={streamingStage} />}
            {demoAnswer && <article className="assistant-bubble result">{demoAnswer.document ? <EvidencePanel answer={demoAnswer.document} /> : <p>{demoAnswer.answer}</p>}{demoAnswer.structured && <StructuredEvidence answer={demoAnswer} />}</article>}
            {completionMessage && <p className="assistant-complete-status" role="status">{completionMessage}</p>}
            {requestError && <div className="assistant-error" role="alert"><span>{requestError}</span><button type="button" onClick={retryLastQuestion}>Retry</button></div>}
          </div>
          <form className="chat-form" onSubmit={submitDemo}>
            {busy && <AssistantActivity stage={streamingStage} nearComposer />}
            <textarea aria-label="Assistant question" disabled={busy} value={demoQuestion} onChange={(event) => setDemoQuestion(event.target.value)} placeholder="Ask about authorized bills, leases, estates, plots, policies or cases." rows={2} />
            {busy ? <button type="button" onClick={stopAssistantRequest}><Icon name="refresh" size={18} /> Stop</button> : <button type="submit"><Icon name="send" size={18} /> Send</button>}
          </form>
        </section>
        <aside className="assistant-right">
          <Panel title="Evidence and Context" icon="file">
            <ContextDrawer timeline={timeline} demoAnswer={demoAnswer} />
          </Panel>
          {timeline && <WorkflowActions accessToken={accessToken} demoActive={demoActive} me={me} timeline={timeline} handoffToNo={handoffToNo} verifyByNo={verifyByNo} forwardToHod={forwardToHod} />}
          {timeline && (
            <Panel title="Case Observation" icon="message">
              <form className="message-form" onSubmit={demoActive ? sendDemoCaseMessage : sendMessage}>
                <input aria-label="Case observation" value={demoActive ? demoCaseMessage : caseMessage} onChange={(event) => demoActive ? setDemoCaseMessage(event.target.value) : setCaseMessage(event.target.value)} />
                <button type="submit">Record</button>
              </form>
            </Panel>
          )}
        </aside>
      </section>
    </>
  );
}

interface AssistantProps {
  accessToken: string;
  me: Me;
  cases: CaseRecord[];
  chats: ChatSummary[];
  selectedChat: ChatResponse | null;
  selectedChatId?: string;
  timeline: CaseTimeline | null;
  demoActive: boolean;
  demoAnswer: DemoAnswer | null;
  busy: boolean;
  caseMessage: string;
  demoCaseMessage: string;
  demoQuestion: string;
  streamingStage: string | null;
  requestError: string | null;
  completionMessage: string | null;
  selectCase: (caseId: string) => Promise<void>;
  selectChat: (chatId: string) => Promise<void>;
  createNewChat: () => Promise<void>;
  renameChat: (chatId: string, title: string) => Promise<void>;
  stopAssistantRequest: () => void;
  retryLastQuestion: () => void;
  createDemoCase: () => void;
  sendMessage: (event: FormEvent) => void;
  sendDemoCaseMessage: (event: FormEvent) => void;
  handoffToNo: (recipient: StaffRecipient, remarks: string) => void;
  verifyByNo: () => void;
  forwardToHod: (recipient: StaffRecipient, remarks: string) => void;
  submitDemo: (event: FormEvent) => void;
  setDemoQuestion: (value: string) => void;
  setCaseMessage: (value: string) => void;
  setDemoCaseMessage: (value: string) => void;
  selectedCaseId?: string;
}

function StructuredEvidence({ answer }: { answer: DemoAnswer }) {
  if (!answer.structured) return null;
  return <DataTable columns={[{ key: "facts", label: "Structured facts" }]} rows={answer.structured.rows.map((row) => ({ facts: Object.entries(row).filter(([key]) => key !== "source_refreshed_at").map(([key, value]) => `${key.replaceAll("_", " ")}: ${value ?? "not recorded"}`).join("; ") }))} />;
}

function ContextDrawer({ timeline, demoAnswer }: { timeline: CaseTimeline | null; demoAnswer: DemoAnswer | null }) {
  const capsule = timeline?.capsules.at(-1);
  return (
    <div className="context-tabs">
      <h3>Sources</h3>
      {demoAnswer?.document ? <EvidencePanel answer={demoAnswer.document} /> : <p>Document sources appear after a RAG answer.</p>}
      <h3>Context Capsule</h3>
      {capsule ? <><p><strong>Case:</strong> {timeline?.case.case_id}</p><p><strong>State:</strong> {capsule.current_state.replaceAll("_", " ")}</p><p>{capsule.rolling_summary}</p></> : <p>No case capsule is open.</p>}
      <h3>Query Details</h3>
      <p>{demoAnswer?.structured ? demoAnswer.structured.database_objects.join(", ") : "No structured query has run in this conversation."}</p>
    </div>
  );
}

function WorkflowActions({ accessToken, demoActive, me, timeline, handoffToNo, verifyByNo, forwardToHod }: { accessToken: string; demoActive: boolean; me: Me; timeline: CaseTimeline; handoffToNo: (recipient: StaffRecipient, remarks: string) => void; verifyByNo: () => void; forwardToHod: (recipient: StaffRecipient, remarks: string) => void }) {
  const isDo = me.roles.includes("Data Entry Operator");
  const isNo = me.roles.includes("Nodal/Regional Officer");
  const handoffRole: LocalLoginRole | null = isDo && ["draft", "returned_to_do"].includes(timeline.case.state)
    ? "Nodal/Regional Officer"
    : isNo && timeline.case.state === "verified_by_no"
      ? "HOD"
      : null;
  const [recipients, setRecipients] = useState<StaffRecipient[]>([]);
  const [recipientSubject, setRecipientSubject] = useState("");
  const [remarks, setRemarks] = useState("");
  const [recipientError, setRecipientError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (handoffRole === null || demoActive) {
      setRecipients([]);
      setRecipientSubject("");
      return () => { cancelled = true; };
    }
    void loadCaseRecipients(handoffRole, accessToken).then((items) => {
      if (cancelled) return;
      setRecipients(items);
      setRecipientSubject(items[0]?.subject ?? "");
      setRecipientError(items.length === 0 ? "No eligible recipient is available in your department and unit." : null);
    }).catch(() => {
      if (!cancelled) {
        setRecipients([]);
        setRecipientSubject("");
        setRecipientError("Recipient list could not be loaded.");
      }
    });
    return () => { cancelled = true; };
  }, [accessToken, demoActive, handoffRole]);
  const selectedRecipient = recipients.find((item) => item.subject === recipientSubject);
  return (
    <Panel title="Workflow" icon="shield">
      <ol className="timeline-list">{timeline.transitions.map((item) => <li key={item.transition_id}><strong>{item.to_state.replaceAll("_", " ")}</strong><span>{item.actor_role} - {new Date(item.occurred_at).toLocaleString()}</span></li>)}</ol>
      <div className="handoff-actions">
        {handoffRole && !demoActive && <>
          <label>Share full case transcript with {handoffRole}
            <select aria-label={`Recipient ${handoffRole}`} value={recipientSubject} onChange={(event) => setRecipientSubject(event.target.value)}>
              {recipients.map((recipient) => <option key={recipient.subject} value={recipient.subject}>{recipient.display_name}{recipient.designation ? ` — ${recipient.designation}` : ""}</option>)}
            </select>
          </label>
          <label>Handoff note
            <textarea aria-label="Handoff note" value={remarks} onChange={(event) => setRemarks(event.target.value)} rows={2} />
          </label>
          {recipientError && <p className="workflow-error" role="alert">{recipientError}</p>}
          {isDo && <button disabled={!selectedRecipient || !remarks.trim()} onClick={() => selectedRecipient && handoffToNo(selectedRecipient, remarks.trim())} type="button">Submit to NO</button>}
          {isNo && <button disabled={!selectedRecipient || !remarks.trim()} onClick={() => selectedRecipient && forwardToHod(selectedRecipient, remarks.trim())} type="button">Submit to HOD</button>}
        </>}
        {isNo && timeline.case.state === "submitted_to_no" && <button onClick={verifyByNo} type="button">Verify</button>}
      </div>
    </Panel>
  );
}

function AuditPage({ audit }: { audit: AuditEvent[] }) {
  return (
    <>
      <PageHeader title="Audit Logs" subtitle="Immutable read-only activity visible to the current server-issued role." />
      <FilterBar><SearchInput label="Filter audit rows" value="" onChange={() => undefined} /><label>Result<select><option>All results</option><option>ALLOWED</option><option>DENIED</option><option>ERROR</option></select></label></FilterBar>
      <Panel title="Visible Activity" icon="file">
        <DataTable
          columns={[{ key: "time", label: "Timestamp" }, { key: "action", label: "Action" }, { key: "result", label: "Result" }, { key: "correlation", label: "Correlation ID" }]}
          rows={audit.map((item) => ({ time: new Date(item.occurred_at).toLocaleString(), action: item.query_category, result: item.result_status, correlation: item.correlation_id }))}
        />
      </Panel>
    </>
  );
}

function ApprovalsPage({ cases }: { cases: CaseRecord[] }) {
  const reviewCases = cases.filter((item) => ["submitted_to_no", "verified_by_no", "submitted_to_hod", "returned_to_no"].includes(item.state));
  return (
    <>
      <PageHeader title="Approval Queue" subtitle="Conditional review queue for Nodal/Regional Officer and HOD roles." />
      <Panel title="Cases awaiting review" icon="shield"><CaseTable cases={reviewCases} /></Panel>
    </>
  );
}

function AnalyticsPage() {
  return (
    <>
      <PageHeader title="Analytics" subtitle="Role-scoped analytics area. Backend aggregation endpoints are still required for operational charts." />
      <section className="dashboard-grid">
        <Panel title="Estate Portfolio" icon="map"><Unavailable capability="Estate portfolio aggregation endpoint" /></Panel>
        <Panel title="Revenue and Outstanding" icon="rupee"><Unavailable capability="Revenue aggregation endpoint" /></Panel>
        <Panel title="Workflow SLA" icon="calendar"><Unavailable capability="Workflow SLA aggregation endpoint" /></Panel>
        <Panel title="Document Ingestion" icon="upload"><Unavailable capability="Document ingestion analytics endpoint" /></Panel>
      </section>
    </>
  );
}

function ForecastingPage() {
  return <><PageHeader title="Forecasting" subtitle="Forecast outputs must include model, version, training period, metrics and limitations." /><Panel title="Forecast overview" icon="bar"><Unavailable capability="Approved forecast result endpoint" /></Panel></>;
}

function LegalCasesPage() {
  return <><PageHeader title="Legal Cases" subtitle="Governed estate/legal module awaiting backend list and detail APIs." /><Panel title="Legal case register" icon="building"><Unavailable capability="Legal case register endpoint" /></Panel></>;
}

function NotFoundPage() {
  return <div className="state-card denied-state"><h1>404</h1><h2>Page not found</h2><p>The requested route is not part of the AI Powered Port Management System.</p><Link to="/dashboard">Return to dashboard</Link></div>;
}

function AppRuntime() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selectedChat, setSelectedChat] = useState<ChatResponse | null>(null);
  const [timeline, setTimeline] = useState<CaseTimeline | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [localAuthAvailable, setLocalAuthAvailable] = useState(false);
  const [demoActive, setDemoActive] = useState(false);
  const [demoQuestion, setDemoQuestion] = useState("");
  const [demoAnswer, setDemoAnswer] = useState<DemoAnswer | null>(null);
  const [streamingStage, setStreamingStage] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [completionMessage, setCompletionMessage] = useState<string | null>(null);
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const [demoCaseMessage, setDemoCaseMessage] = useState("");
  const [caseMessage, setCaseMessage] = useState("");
  const accessToken = window.pmsAuth?.accessToken ?? "";
  const { pathname, navigate } = useRouter();

  async function refreshCases(token = accessToken) {
    setCases(await loadCaseQueue(token));
  }

  async function refreshChats(token = accessToken) {
    try {
      setChats(await loadChats(token));
    } catch {
      setChats([]);
    }
  }

  useEffect(() => {
    setBusy(true);
    async function restoreSession() {
      try {
        const localStatus = await loadLocalAuthStatus();
        setLocalAuthAvailable(localStatus.enabled);
      } catch {
        setLocalAuthAvailable(false);
      }
      let controlledDemoEnabled = false;
      try {
        const status = await loadDemoStatus();
        controlledDemoEnabled = status.enabled;
        setDemoAvailable(status.enabled);
      } catch {
        setDemoAvailable(false);
      }
      if (controlledDemoEnabled) {
        try {
          const identity = await loadDemoMe();
          setAuthenticated(true);
          setMe(identity);
          setDemoActive(true);
          setError(null);
          setCases(await loadCaseQueue(""));
          await refreshChats("");
          try { setAudit(await loadAudit("")); } catch { setAudit([]); }
          setAuthChecked(true);
          return;
        } catch (reason) {
          if (!(reason instanceof ApiError && reason.status === 401)) setError(errorMessage(reason));
        }
      }
      try {
        const identity = await loadMe(accessToken);
        setAuthenticated(true);
        setMe(identity);
        setError(null);
        if (!identity.roles.includes("Tenant")) setCases(await loadCaseQueue(accessToken));
        await refreshChats(accessToken);
        try { setAudit(await loadAudit(accessToken)); } catch { setAudit([]); }
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 401) {
          setAuthenticated(false);
          setError(null);
        } else setError(errorMessage(reason));
      } finally {
        setAuthChecked(true);
        setBusy(false);
      }
    }
    void restoreSession();
  }, [accessToken]);

  async function selectCase(caseId: string) {
    try {
      setError(null);
      setTimeline(await loadTimeline(caseId, demoActive ? "" : accessToken));
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function startDemo(identity: DemoIdentity) {
    try {
      setBusy(true);
      setError(null);
      const demoIdentity = await startDemoSession(identity);
      setMe(demoIdentity);
      setCases(await loadCaseQueue(""));
      await refreshChats("");
      try { setAudit(await loadAudit("")); } catch { setAudit([]); }
      setDemoActive(true);
      setAuthenticated(true);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function startLocalLogin(username: string, password: string, role: LocalLoginRole) {
    try {
      setBusy(true);
      setError(null);
      const identity = await loginLocally(username, password, role);
      setMe(identity);
      setAuthenticated(true);
      setDemoActive(false);
      if (!identity.roles.includes("Tenant")) await refreshCases("");
      await refreshChats("");
      try { setAudit(await loadAudit("")); } catch { setAudit([]); }
      navigate("/dashboard", { replace: true });
    } catch (reason) {
      setAuthenticated(false);
      setMe(null);
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function exitDemo() {
    try { await endDemoSession(); } catch { /* Local UI exits even if API stopped. */ }
    setDemoActive(false);
    setAuthenticated(false);
    setMe(null);
    setDemoAnswer(null);
    setTimeline(null);
    setCases([]);
    setChats([]);
    setSelectedChat(null);
    setAudit([]);
    navigate("/", { replace: true });
  }

  async function returnHome() {
    try {
      if (demoActive) await endDemoSession();
      else await endLocalSession();
    } catch {
      /* The public landing page remains available even if the API is not running. */
    }
    setDemoActive(false);
    setAuthenticated(false);
    setMe(null);
    setDemoAnswer(null);
    setTimeline(null);
    setCases([]);
    setChats([]);
    setSelectedChat(null);
    setAudit([]);
    navigate("/", { replace: true });
  }

  async function createDemoCase() {
    try {
      setBusy(true);
      const item = await createCase({
        title: "Review of approved lease and applicable land-policy provision",
        objective: "Controlled local review of approved lease summaries and indexed land-management evidence.",
        initial_message: "Controlled local case created. No unverified operational assertion is made.",
        unit_id: "land"
      }, demoActive ? "" : accessToken);
      await refreshCases(demoActive ? "" : accessToken);
      setTimeline(await loadTimeline(item.case_id, demoActive ? "" : accessToken));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function selectChat(chatId: string) {
    try {
      setError(null);
      const chat = await loadChat(chatId, demoActive ? "" : accessToken);
      setSelectedChat(chat);
      navigate(`/assistant/chats/${chatId}`);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function renameChat(chatId: string, title: string) {
    const renamed = await updateChat(chatId, { title }, demoActive ? "" : accessToken);
    setSelectedChat(renamed);
    await refreshChats(demoActive ? "" : accessToken);
  }

  async function createNewChat() {
    try {
      setBusy(true);
      setError(null);
      const chat = await createChat({ title: "New Chat", chat_type: "PERSONAL" }, demoActive ? "" : accessToken);
      setSelectedChat(chat);
      await refreshChats(demoActive ? "" : accessToken);
      navigate(`/assistant/chats/${chat.chat_id}`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitQuestion(question: string) {
    if (!question.trim() || !me || busy || activeRequest.current !== null) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      setBusy(true);
      setRequestError(null);
      setCompletionMessage(null);
      setStreamingStage("reading_question");
      const document = await runPolicyQueryStream(
        question.trim(),
        demoActive ? "" : accessToken,
        (stage) => setStreamingStage(stage),
        controller.signal,
        selectedChat?.chat_id
      );
      const result = documentAnswerForWorkspace(document, me);
      setDemoAnswer(result);
      setCompletionMessage("Answer validated and ready.");
      if (timeline && result.route !== "REQUEST_REFUSED" && !result.review_required) {
        await postCaseMessage(timeline.case.case_id, `Assistant result: ${result.answer}`, demoActive ? "" : accessToken);
        setTimeline(await loadTimeline(timeline.case.case_id, demoActive ? "" : accessToken));
      }
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setRequestError(errorMessage(reason));
    } finally {
      activeRequest.current = null;
      setStreamingStage(null);
      setBusy(false);
    }
  }

  async function submitDemo(event: FormEvent) {
    event.preventDefault();
    const question = demoQuestion.trim();
    if (!question) return;
    setLastQuestion(question);
    await submitQuestion(question);
  }

  function retryLastQuestion() {
    if (lastQuestion) void submitQuestion(lastQuestion);
  }

  function stopAssistantRequest() {
    activeRequest.current?.abort();
    activeRequest.current = null;
    setStreamingStage(null);
    setBusy(false);
    setRequestError("The request was cancelled. You can retry it when ready.");
  }

  async function sendCaseMessage(event: FormEvent) {
    event.preventDefault();
    if (!timeline || !caseMessage.trim()) return;
    await postCaseMessage(timeline.case.case_id, caseMessage.trim(), accessToken);
    setCaseMessage("");
    setTimeline(await loadTimeline(timeline.case.case_id, accessToken));
  }

  async function sendDemoCaseMessage(event: FormEvent) {
    event.preventDefault();
    if (!timeline || !demoCaseMessage.trim()) return;
    await postCaseMessage(timeline.case.case_id, demoCaseMessage.trim(), "");
    setDemoCaseMessage("");
    setTimeline(await loadTimeline(timeline.case.case_id, ""));
  }

  async function handoffToNo(recipient: StaffRecipient, remarks: string) {
    if (!timeline) return;
    await submitToNo(timeline.case.case_id, demoActive ? "" : accessToken, recipient.subject, remarks);
    setTimeline(await loadTimeline(timeline.case.case_id, demoActive ? "" : accessToken));
    await refreshCases(demoActive ? "" : accessToken);
  }

  async function verifyByNo() {
    if (!timeline) return;
    await verifyCase(timeline.case.case_id, demoActive ? "" : accessToken);
    setTimeline(await loadTimeline(timeline.case.case_id, demoActive ? "" : accessToken));
    await refreshCases(demoActive ? "" : accessToken);
  }

  async function forwardToHod(recipient: StaffRecipient, remarks: string) {
    if (!timeline) return;
    await submitToHod(timeline.case.case_id, demoActive ? "" : accessToken, recipient.subject, remarks);
    setTimeline(await loadTimeline(timeline.case.case_id, demoActive ? "" : accessToken));
    await refreshCases(demoActive ? "" : accessToken);
  }

  if (!authChecked) return <LoadingSkeleton label="Checking your protected PMS session." />;
  if (!authenticated || !me || pathname === "/") return <PublicLogin error={error} demoAvailable={demoAvailable} localAuthAvailable={localAuthAvailable} startDemo={startDemo} startLocalLogin={startLocalLogin} />;
  const currentMe = me;

  const assistantProps: AssistantProps = {
    accessToken: demoActive ? "" : accessToken,
    me: currentMe,
    cases,
    chats,
    selectedChat,
    timeline,
    demoActive,
    demoAnswer,
    busy,
    caseMessage,
    demoCaseMessage,
    demoQuestion,
    streamingStage,
    requestError,
    completionMessage,
    selectCase,
    selectChat,
    createNewChat,
    renameChat,
    stopAssistantRequest,
    retryLastQuestion,
    createDemoCase,
    sendMessage: sendCaseMessage,
    sendDemoCaseMessage,
    handoffToNo,
    verifyByNo,
    forwardToHod,
    submitDemo,
    setDemoQuestion,
    setCaseMessage,
    setDemoCaseMessage
  };

  const currentPage = renderRoute(pathname);

  function renderRoute(path: string): ReactNode {
    if (path === "/dashboard") return <DashboardPage me={currentMe} cases={cases} audit={audit} demoActive={demoActive} />;
    if (path === "/tenants") return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><RegistryPage module="tenants" /></ProtectedRoute>;
    let params = matchRoute("/tenants/:tenantId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><DetailPage kind="tenant" id={params.tenantId} /></ProtectedRoute>;
    if (path === "/leases") return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><RegistryPage module="leases" /></ProtectedRoute>;
    params = matchRoute("/leases/:leaseId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><DetailPage kind="lease" id={params.leaseId} /></ProtectedRoute>;
    if (path === "/land") return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><RegistryPage module="land" /></ProtectedRoute>;
    params = matchRoute("/land/plots/:plotId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><DetailPage kind="plot" id={params.plotId} /></ProtectedRoute>;
    if (path === "/policies") return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><PolicyPage /></ProtectedRoute>;
    params = matchRoute("/policies/:documentId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><PolicyDetailPage documentId={params.documentId} /></ProtectedRoute>;
    if (path === "/documents") return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><DocumentsPage accessToken={demoActive ? "" : accessToken} /></ProtectedRoute>;
    params = matchRoute("/documents/:documentId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><DocumentDetailPage accessToken={demoActive ? "" : accessToken} documentId={params.documentId} /></ProtectedRoute>;
    if (path === "/assistant") return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><AssistantPage {...assistantProps} /></ProtectedRoute>;
    params = matchRoute("/assistant/chats/:chatId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={["Tenant", ...ALL_STAFF_ROLES]}><AssistantPage {...assistantProps} selectedChatId={params.chatId} /></ProtectedRoute>;
    params = matchRoute("/assistant/cases/:caseId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><AssistantPage {...assistantProps} selectedCaseId={params.caseId} /></ProtectedRoute>;
    if (path === "/analytics") return <ProtectedRoute me={currentMe} allowed={ALL_STAFF_ROLES}><AnalyticsPage /></ProtectedRoute>;
    if (path === "/audit") return <ProtectedRoute me={currentMe} allowed={REVIEW_ROLES}><AuditPage audit={audit} /></ProtectedRoute>;
    if (path === "/approvals") return <ProtectedRoute me={currentMe} allowed={REVIEW_ROLES}><ApprovalsPage cases={cases} /></ProtectedRoute>;
    params = matchRoute("/approvals/:caseId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={REVIEW_ROLES}><AssistantPage {...assistantProps} selectedCaseId={params.caseId} /></ProtectedRoute>;
    if (path === "/forecasting") return <ProtectedRoute me={currentMe} allowed={HOD_ONLY}><ForecastingPage /></ProtectedRoute>;
    if (path === "/legal-cases") return <ProtectedRoute me={currentMe} allowed={REVIEW_ROLES}><LegalCasesPage /></ProtectedRoute>;
    params = matchRoute("/legal-cases/:caseId", path);
    if (params) return <ProtectedRoute me={currentMe} allowed={REVIEW_ROLES}><DetailPage kind="legal" id={params.caseId} /></ProtectedRoute>;
    return <NotFoundPage />;
  }

  return <AppShell me={currentMe} demoActive={demoActive} exitDemo={exitDemo} returnHome={returnHome}>{error && <div className="notice" role="alert">{error}</div>}{currentPage}</AppShell>;
}

export default function App() {
  return <BrowserRouter><AppRuntime /></BrowserRouter>;
}
