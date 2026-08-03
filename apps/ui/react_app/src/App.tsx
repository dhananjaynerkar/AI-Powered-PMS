import { FormEvent, type ReactNode, useEffect, useState } from "react";

import {
  ApiError,
  createCase,
  endDemoSession,
  loadAudit,
  loadCaseQueue,
  loadDemoMe,
  loadDemoStatus,
  loadLocalAuthStatus,
  loadMe,
  loadTimeline,
  loginLocally,
  logout,
  postCaseMessage,
  runPolicyQuery,
  runDemoQuery,
  runStructuredQuery,
  startDemoSession,
  submitToHod,
  submitToNo,
  verifyCase
} from "./api";
import type {
  AuditEvent,
  CaseRecord,
  CaseTimeline,
  DemoAnswer,
  DemoIdentity,
  GroundedAnswer,
  LocalLoginRole,
  Me,
  StructuredAnswer
} from "./types";
import "./styles.css";

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Request failed";
}

function isDenied(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 403;
}

function isTenant(identity: Me | null): boolean {
  return identity?.roles.includes("Tenant") ?? false;
}

function renderOperationalRow(row: Record<string, unknown>): string {
  return Object.entries(row)
    .filter(([column]) => column !== "source_refreshed_at")
    .map(([column, value]) => `${column.replaceAll("_", " ")}: ${value === null ? "not recorded" : String(value)}`)
    .join(" · ");
}

function StatusPill({ children, tone = "ready" }: { children: string; tone?: "ready" | "pending" }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function AppBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="app-brand">
      <div className="brand-mark" aria-hidden>⚓</div>
      <div>
        <p className="brand-overline">Indian Port Estate Services</p>
        <strong>{compact ? "AI Powered PMS" : "AI Powered Port Management System"}</strong>
      </div>
    </div>
  );
}

type PublicPage = "home" | "policies" | "about" | "help" | "contact";

function PublicFooter({ setPage }: { setPage: (page: PublicPage) => void }) {
  return <footer className="public-footer"><div className="public-footer-grid"><section><strong>AI Powered Port Management System</strong><p>Local, evidence-led port estate services prototype.</p></section><section><strong>Explore</strong><button onClick={() => setPage("policies")} type="button">Policy evidence</button><button onClick={() => setPage("about")} type="button">About the platform</button><button onClick={() => setPage("help")} type="button">Help</button></section><section><strong>Safety</strong><p>Server-enforced access</p><p>Source citations</p><p>Audited activity</p></section><section><strong>Prototype status</strong><p>Local development only</p><p>Not an official government website</p></section></div><div className="public-footer-bottom">© {new Date().getFullYear()} AI Powered PMS · Local demonstration</div></footer>;
}

function LocalPasswordLogin({ startLocalLogin }: { startLocalLogin: (username: string, password: string, role: LocalLoginRole) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<LocalLoginRole>("Data Entry Operator");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (username.trim() && password) startLocalLogin(username.trim(), password, role);
  }
  return <form className="local-login-form" onSubmit={submit}><label>Role<select value={role} onChange={(event) => setRole(event.target.value as LocalLoginRole)}><option>Data Entry Operator</option><option>Nodal/Regional Officer</option><option>HOD</option><option>Tenant</option></select></label><label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label><label>Password<input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><button className="gold-action" type="submit">Sign in securely <span>→</span></button><p className="demo-gate-note">Local development login only. The server verifies an scrypt password hash and sets an HttpOnly session cookie.</p></form>;
}

function LoginScreen({ error, demoAvailable, localAuthAvailable, startDemo, startLocalLogin }: { error: string | null; demoAvailable: boolean; localAuthAvailable: boolean; startDemo: (identity: DemoIdentity) => void; startLocalLogin: (username: string, password: string, role: LocalLoginRole) => void }) {
  const [page, setPage] = useState<PublicPage>("home");
  const nav = <nav aria-label="Public portal navigation">{(["home", "policies", "about", "help", "contact"] as const).map((item) => <button className={page === item ? "active" : ""} key={item} onClick={() => setPage(item)} type="button">{item === "home" ? "Home" : item === "policies" ? "Policy evidence" : item[0].toUpperCase() + item.slice(1)}</button>)}</nav>;
  const accessActions = localAuthAvailable ? <LocalPasswordLogin startLocalLogin={startLocalLogin} /> : demoAvailable ? <div className="portal-actions"><button className="gold-action" onClick={() => startDemo("demo.do")} type="button">Start as DO <span>→</span></button><button className="outline-action" onClick={() => startDemo("demo.no")} type="button">Review as NO</button><button className="outline-action" onClick={() => startDemo("demo.hod")} type="button">Review as HOD</button></div> : <p className="demo-gate-note">The controlled demo is unavailable. It requires the existing localhost development gate; live Keycloak login is intentionally not used for this client demonstration.</p>;
  let content: ReactNode;
  if (page === "policies") {
    content = <section className="public-content policy-layout"><aside className="policy-list"><p className="eyebrow">Verified repository</p><h1>Policy evidence</h1><p>Only accepted and indexed documents are displayed.</p><button className="selected-policy" type="button"><strong>Clarification Circular (Land Management) No. 2 of 2019</strong><span>Accepted internal evidence · page 2</span></button><p className="muted">More documents appear only after acceptance and indexing.</p></aside><article className="policy-detail"><span className="policy-tag">Land management · accepted</span><h2>Clarification Circular (Land Management) No. 2 of 2019</h2><p>Verified evidence is available from <strong>Clarification 1</strong> on page 2. The controlled assistant returns this passage with its source citation; it does not determine an individual tenant’s eligibility or dues.</p><div className="policy-stat-grid"><div><span>Evidence page</span><strong>2</strong></div><div><span>Status</span><strong>Accepted</strong></div><div><span>Classification</span><strong>Internal</strong></div></div>{demoAvailable ? <button onClick={() => startDemo("demo.do")} type="button">Open controlled evidence demo</button> : <p className="demo-gate-note">Controlled evidence access is unavailable until the local demo gate is enabled.</p>}</article></section>;
  } else if (page === "about") {
    content = <section className="public-content about-content"><p className="eyebrow">About the platform</p><h1>Evidence-led port estate operations</h1><p className="lead">A local system for authorized document evidence, approved operational queries and traceable case collaboration.</p><div className="feature-grid"><article><span>◈</span><h2>Secure by design</h2><p>Identity, RBAC, RLS and document ACL checks occur before retrieval.</p></article><article><span>✦</span><h2>Grounded answers</h2><p>Document responses carry validated citations instead of unsupported claims.</p></article><article><span>▦</span><h2>Trusted operations</h2><p>Structured questions use approved, read-only query templates.</p></article><article><span>▤</span><h2>Shared continuity</h2><p>Case messages, handoffs and context capsules preserve workflow evidence.</p></article></div></section>;
  } else if (page === "help") {
    content = <section className="public-content narrow-content"><p className="eyebrow">Help and safeguards</p><h1>Before you use the assistant</h1><div className="faq-list"><details open><summary>How is access protected?</summary><p>The server validates the identity and applies role, classification, document ACL and database policy controls. The frontend is not the security boundary.</p></details><details><summary>What can the assistant answer?</summary><p>It can return authorized document evidence and approved operational queries. Insufficient evidence returns review required.</p></details><details><summary>Does it accept arbitrary SQL?</summary><p>No. Operational access is limited to approved, parameterized, read-only query templates.</p></details><details><summary>Is this a public production service?</summary><p>No. This interface is a local prototype and is not an official government website.</p></details></div></section>;
  } else if (page === "contact") {
    content = <section className="public-content contact-content"><div><p className="eyebrow">Project contact</p><h1>Request a controlled demonstration</h1><p className="lead">This local prototype does not send messages or collect personal contact information through the browser.</p><div className="contact-card"><strong>Safe next step</strong><p>Use the approved project communication channel to arrange a review. Do not place credentials or sensitive case information in a web form.</p></div></div><aside><span>⌁</span><h2>Local-only scope</h2><p>Authentication, retrieval, query and case routes remain on the configured local environment.</p></aside></section>;
  } else {
    content = <><section className="public-hero"><div><p className="hero-label">Port estate services · local prototype</p><h1>Intelligent evidence for port land and lease management.</h1><p>Bring authorized policy evidence, approved PostgreSQL facts and cross-role case continuity into one controlled workflow.</p>{accessActions}<p className="keycloak-parked">Live Keycloak sign-in is retained in the application but parked for this local client demonstration.</p></div><aside className="hero-evidence"><p>CONTROLLED DEMONSTRATION</p><h2>Ask with evidence, not assumptions.</h2><ul><li>Authorized policy retrieval with page citations</li><li>Approved read-only operational queries</li><li>Persistent DO → NO → HOD case context</li></ul><span>Local only · production approval required</span></aside></section><section className="public-section"><p className="eyebrow">Platform modules</p><h2>One controlled system for port estate work</h2><div className="feature-grid"><article><span>◉</span><h3>Evidence assistant</h3><p>Find accepted policy evidence with source and page references.</p></article><article><span>⌂</span><h3>Tenant access</h3><p>Tenant demonstration remains outside this controlled officer-only demo.</p></article><article><span>▦</span><h3>Authority workflow</h3><p>DO, NO and HOD work from a shared, auditable case timeline.</p></article><article><span>▤</span><h3>Approved data</h3><p>Structured answers come only from governed read-only views.</p></article></div></section><section className="trust-band"><div><p className="eyebrow">Security and architecture</p><h2>Traceable by design</h2><p>Authorization before retrieval, citations before answers, and audits for sensitive activity.</p></div><div className="trust-chips"><span>Controlled local demo</span><span>RBAC + RLS</span><span>PostgreSQL</span><span>pgvector</span><span>Hybrid retrieval</span><span>Audit trail</span></div></section><section className="public-cta"><div><p className="eyebrow">Client demonstration</p><h2>Open the controlled officer workflow</h2><p>Use the same case as DO, NO and HOD to show approved evidence and auditable role handoff.</p></div>{accessActions}</section></>;
  }
  return <main className="public-portal"><div className="public-topbar"><span>AI Powered PMS · Local on-premises prototype</span><span>Not an official Government of India website</span></div><header className="public-header"><AppBrand />{nav}</header>{error && <div className="public-alert" role="alert">{error}</div>}{content}{demoAvailable && !localAuthAvailable && page === "home" && <section className="demo-launch"><div><p className="eyebrow">Local controlled demo</p><strong>Server-issued officer identities only</strong><span>DO, NO and HOD are mapped by the local server. No Keycloak credential, browser role or tenant record is used.</span></div><div><button onClick={() => startDemo("demo.do")} type="button">Try as DO</button><button onClick={() => startDemo("demo.no")} type="button">Try as NO</button><button onClick={() => startDemo("demo.hod")} type="button">Try as HOD</button></div></section>}<PublicFooter setPage={setPage} /></main>;
}

function DemoWorkspace({ me, cases, timeline, question, answer, caseMessage, busy, error, setQuestion, setCaseMessage, submit, createDemoCase, selectCase, sendCaseMessage, handoffToNo, verifyByNo, forwardToHod, exit }: { me: Me; cases: CaseRecord[]; timeline: CaseTimeline | null; question: string; answer: DemoAnswer | null; caseMessage: string; busy: boolean; error: string | null; setQuestion: (value: string) => void; setCaseMessage: (value: string) => void; submit: (event: FormEvent) => void; createDemoCase: () => void; selectCase: (caseId: string) => void; sendCaseMessage: (event: FormEvent) => void; handoffToNo: () => void; verifyByNo: () => void; forwardToHod: () => void; exit: () => void }) {
  const routeLabel = answer?.route.replaceAll("_", " ");
  const isDo = me.roles.includes("Data Entry Operator");
  const isNo = me.roles.includes("Nodal/Regional Officer");
  const isHod = me.roles.includes("HOD");
  return (
    <main className="workspace-shell demo-workspace">
      <div className="demo-banner"><strong>LOCAL CONTROLLED DEMO</strong><span>Read-only sample access. Not approved for production use.</span></div>
      <header className="workspace-header">
        <AppBrand compact />
        <div className="workspace-title"><h1>Case-to-evidence demo</h1><p>One persistent DO → NO → HOD case with governed PostgreSQL and document evidence</p></div>
        <div className="identity-card"><span className="identity-initial">D</span><div><strong>{me.subject}</strong><span>{me.roles.join(", ")}</span></div><button className="sign-out" onClick={exit} type="button">Exit demo</button></div>
      </header>
      {busy && <div className="notice">Running the approved local route…</div>}
      {error && <div className="denied" role="alert">{error}</div>}
      <section className="demo-case-grid">
        <aside className="content-card"><div className="card-heading"><div><p className="eyebrow">Persistent workflow</p><h2>Controlled case</h2></div><StatusPill>{`${cases.length} visible`}</StatusPill></div>{isDo && <button onClick={createDemoCase} type="button">Create or open demo case</button>}{cases.map((item) => <button className="case-card" key={item.case_id} onClick={() => selectCase(item.case_id)} type="button"><strong>{item.title}</strong><span>{item.state.replaceAll("_", " ")}</span><small>{item.case_id}</small></button>)}</aside>
        <section className="content-card"><p className="eyebrow">Case state</p>{timeline ? <><h2>{timeline.case.title}</h2><p><strong>Case ID:</strong> {timeline.case.case_id}</p><p><strong>State:</strong> {timeline.case.state.replaceAll("_", " ")}</p><p><strong>Current owner:</strong> {timeline.case.current_owner_subject}</p><div className="demo-actions">{isDo && timeline.case.state === "draft" && <button onClick={handoffToNo} type="button">Forward to NO</button>}{isNo && timeline.case.state === "submitted_to_no" && <button onClick={verifyByNo} type="button">Record verification</button>}{isNo && timeline.case.state === "verified_by_no" && <button onClick={forwardToHod} type="button">Forward to HOD</button>}{isHod && timeline.case.state === "submitted_to_hod" && <StatusPill>Record final review below</StatusPill>}</div></> : <p className="empty-state">DO creates the safe fixture; all three roles then open the same server-authorized case.</p>}</section>
        <aside className="content-card capsule-panel"><p className="eyebrow">Context capsule</p>{timeline?.capsules.at(-1) ? <><h2>{timeline.capsules.at(-1)?.objective}</h2><p>{timeline.capsules.at(-1)?.rolling_summary}</p><h3>Next action</h3><p>{timeline.capsules.at(-1)?.required_next_action}</p><h3>Evidence</h3><ul>{timeline.capsules.at(-1)?.evidence.map((item) => <li key={`${item.reference_type}-${item.reference_id}`}>{item.reference_type}: {item.reference_id}</li>)}</ul></> : <p className="empty-state">A bounded capsule is generated at each handoff.</p>}</aside>
      </section>
      <section className="content-card demo-chat">
        <div className="card-heading"><div><p className="eyebrow">Controlled question routing</p><h2>Ask about policy or approved operational data</h2></div><StatusPill>Read only</StatusPill></div>
        <form onSubmit={submit}><textarea aria-label="Demo question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Try: Show five approved lease summaries. Or use the gold policy question from the runbook." rows={4} /><button type="submit">Ask controlled demo</button></form>
        {answer && <div className="answer-box demo-answer"><div className="answer-meta"><StatusPill tone={answer.review_required ? "pending" : "ready"}>{routeLabel ?? "REVIEW REQUIRED"}</StatusPill><span>{answer.duration_ms.toFixed(1)} ms · Correlation {answer.correlation_id}</span></div><p>{answer.answer}</p>{answer.structured && <section><h3>Operational facts from PostgreSQL</h3><p><strong>{answer.structured.query_id}</strong> · {answer.structured.database_objects.join(", ")} · {answer.structured.row_count} matching rows · {answer.structured.read_only ? "read-only" : "not available"}</p><p>{answer.structured.filters.join(" ")}{answer.structured.freshness_at ? ` Source refreshed ${new Date(answer.structured.freshness_at).toLocaleString()}.` : ""}</p>{answer.structured.rows.length > 0 && <ol className="structured-result-lines">{answer.structured.rows.map((row, index) => <li key={index}>{renderOperationalRow(row)}</li>)}</ol>}</section>}{answer.document && <section><h3>Policy evidence from indexed document</h3>{answer.evidence_extracted && <p className="demo-warning">Verified extractive demonstration evidence; no model interpretation was added.</p>}<ul>{answer.document.sources.map((source) => <li key={source.source_id}><strong>{source.document_title}</strong><span>Pages {source.page_numbers.join(", ")}{source.clause_number ? ` · Clause ${source.clause_number}` : ""} · version {source.document_version_id}</span></li>)}</ul></section>}{answer.warnings.map((warning) => <p className="demo-warning" key={warning}>{warning}</p>)}</div>}
      </section>
      {timeline && <section className="content-card demo-timeline"><div className="card-heading"><div><p className="eyebrow">Chronology and observations</p><h2>Same persistent case</h2></div><StatusPill>{`${timeline.messages.length} messages`}</StatusPill></div><div className="message-list">{timeline.messages.map((message) => <article key={message.message_id}><div><strong>#{message.sequence_number} · {message.author_role}</strong><time>{new Date(message.created_at).toLocaleString()}</time></div><p>{message.body}</p></article>)}</div><form className="message-form" onSubmit={sendCaseMessage}><input aria-label="Case observation" value={caseMessage} onChange={(event) => setCaseMessage(event.target.value)} placeholder={isHod ? "Record final demo review status" : "Record an authorized observation"} /><button type="submit">Record observation</button></form></section>}
    </main>
  );
}

interface WorkspaceProps {
  me: Me;
  cases: CaseRecord[];
  timeline: CaseTimeline | null;
  audit: AuditEvent[];
  policyQuestion: string;
  policyAnswer: GroundedAnswer | null;
  structuredQuestion: string;
  structuredAnswer: StructuredAnswer | null;
  messageBody: string;
  busy: boolean;
  error: string | null;
  denial: string | null;
  setPolicyQuestion: (value: string) => void;
  setStructuredQuestion: (value: string) => void;
  setMessageBody: (value: string) => void;
  selectCase: (caseId: string) => void;
  submitPolicy: (event: FormEvent) => void;
  submitStructured: (event: FormEvent) => void;
  sendMessage: (event: FormEvent) => void;
}

function WorkspaceHeader({ me, title, subtitle }: Pick<WorkspaceProps, "me"> & { title: string; subtitle: string }) {
  return (
    <header className="workspace-header">
      <AppBrand compact />
      <div className="workspace-title"><h1>{title}</h1><p>{subtitle}</p></div>
      <div className="identity-card">
        <span className="identity-initial">{me.subject.slice(0, 1).toUpperCase()}</span>
        <div><strong>{me.subject}</strong><span>{me.roles.join(", ")}</span></div>
        <button className="sign-out" onClick={logout} type="button">Sign out</button>
      </div>
    </header>
  );
}

function EvidencePanel({
  policyQuestion,
  policyAnswer,
  setPolicyQuestion,
  submitPolicy
}: Pick<WorkspaceProps, "policyQuestion" | "policyAnswer" | "setPolicyQuestion" | "submitPolicy">) {
  return (
    <section className="content-card evidence-panel">
      <div className="card-heading"><div><p className="eyebrow">Evidence search</p><h2>Authorized policy question</h2></div><StatusPill>Server retrieval</StatusPill></div>
      <form onSubmit={submitPolicy}>
        <textarea aria-label="Policy question" value={policyQuestion} onChange={(event) => setPolicyQuestion(event.target.value)} placeholder="Ask about an authorized policy or clause" rows={3} />
        <button type="submit">Retrieve cited evidence</button>
      </form>
      {policyAnswer && <div className="answer-box"><p>{policyAnswer.answer}</p><div className="answer-meta"><StatusPill tone={policyAnswer.review_required ? "pending" : "ready"}>{policyAnswer.review_required ? "Review required" : "Grounded"}</StatusPill><span>{policyAnswer.confidence}</span></div><ul>{policyAnswer.sources.map((source) => <li key={source.source_id}><strong>{source.document_title}</strong><span>Pages {source.page_numbers.join(", ")}{source.clause_number ? ` · Clause ${source.clause_number}` : ""}</span></li>)}</ul></div>}
    </section>
  );
}

function TenantWorkspace(props: WorkspaceProps) {
  const { me } = props;
  return (
    <main className="workspace-shell">
      <WorkspaceHeader me={me} title="Tenant workspace" subtitle="Your signed tenant scope and authorized evidence" />
      <section className="tenant-banner"><div><p className="eyebrow">Tenant access</p><h2>Welcome to your protected service area</h2><p>Lease, payment, and document records are shown only after the backend verifies your canonical tenant mapping.</p></div><StatusPill>Signed tenant scope</StatusPill></section>
      <section className="summary-grid tenant-summary">
        <article className="summary-card"><p>Canonical tenant scope</p><strong>{me.tenant_id ?? "Not supplied"}</strong><span>This value comes from the signed token, not the browser.</span></article>
        <article className="summary-card"><p>Lease and payment records</p><strong>Not connected</strong><span>No approved tenant lease/payment API exists yet; no values are displayed.</span></article>
        <article className="summary-card"><p>Document access</p><strong>ACL controlled</strong><span>Use evidence search below; document ACL and classification are enforced by the server.</span></article>
      </section>
      <section className="tenant-grid"><EvidencePanel {...props} /><aside className="content-card next-card"><p className="eyebrow">Next verified step</p><h2>Tenant data view</h2><p>Before lease, bill, or payment cards can appear, add an approved API view and activate a canonical tenant mapping.</p><ul><li>No client-provided tenant ID is trusted.</li><li>No sample payments or lease amounts are shown.</li><li>Unauthorized evidence returns a server denial.</li></ul></aside></section>
    </main>
  );
}

function StaffWorkspace(props: WorkspaceProps) {
  const { me, cases, timeline, audit, structuredAnswer, structuredQuestion, messageBody, busy, error, denial } = props;
  return (
    <main className="workspace-shell">
      <WorkspaceHeader me={me} title="Staff workspace" subtitle="Governed case collaboration, evidence, and approved operational queries" />
      {busy && <div className="notice">Working with authorized PMS services…</div>}
      {error && <div className="notice">{error}</div>}
      {denial && <div className="denied" role="alert">{denial}</div>}
      <section className="summary-grid"><article className="summary-card"><p>Authorized case queue</p><strong>{cases.length}</strong><span>Live count returned by the case service.</span></article><article className="summary-card"><p>Role and clearance</p><strong>{me.roles.join(" / ")}</strong><span>{me.classification} classification · {me.department_id ?? "no department"}</span></article><article className="summary-card"><p>Audit visibility</p><strong>{audit.length}</strong><span>Visible audit events for this signed role.</span></article></section>
      <section className="staff-grid">
        <aside className="content-card queue-panel"><div className="card-heading"><div><p className="eyebrow">Workflow</p><h2>Case queue</h2></div><StatusPill>Authorized</StatusPill></div>{cases.length === 0 ? <p className="empty-state">No authorized cases were returned.</p> : cases.map((item) => <button className="case-card" key={item.case_id} onClick={() => props.selectCase(item.case_id)} type="button"><strong>{item.title}</strong><span>{item.state.replaceAll("_", " ")}</span><small>{item.current_owner_role}</small></button>)}</aside>
        <section className="content-card timeline-panel" aria-live="polite"><div className="card-heading"><div><p className="eyebrow">Shared case chat</p><h2>{timeline?.case.title ?? "Select an authorized case"}</h2></div>{timeline && <StatusPill>{timeline.case.state.replaceAll("_", " ")}</StatusPill>}</div>{timeline ? <><div className="message-list">{timeline.messages.map((message) => <article key={message.message_id}><div><strong>#{message.sequence_number} · {message.author_role}</strong><time>{new Date(message.created_at).toLocaleString()}</time></div><p>{message.body}</p></article>)}</div><form className="message-form" onSubmit={props.sendMessage}><input aria-label="Case message" value={messageBody} onChange={(event) => props.setMessageBody(event.target.value)} placeholder="Add an authorized case message" /><button type="submit">Send</button></form></> : <p className="empty-state">Select a case to view its server-authorized timeline and context.</p>}</section>
        <aside className="content-card capsule-panel"><p className="eyebrow">Context capsule</p>{timeline?.capsules.at(-1) ? <><h2>{timeline.capsules.at(-1)?.objective}</h2><p>{timeline.capsules.at(-1)?.rolling_summary}</p><h3>Required next action</h3><p>{timeline.capsules.at(-1)?.required_next_action}</p><h3>Evidence references</h3><ul>{timeline.capsules.at(-1)?.evidence.map((item) => <li key={`${item.reference_type}-${item.reference_id}`}>{item.reference_type}: {item.reference_id}</li>)}</ul></> : <p className="empty-state">A context capsule appears after the first authorized handoff.</p>}</aside>
      </section>
      <section className="query-grid"><EvidencePanel {...props} /><section className="content-card structured-panel"><div className="card-heading"><div><p className="eyebrow">Exact facts</p><h2>Governed structured query</h2></div><StatusPill>Template-only</StatusPill></div><form onSubmit={props.submitStructured}><textarea aria-label="Structured question" value={structuredQuestion} onChange={(event) => props.setStructuredQuestion(event.target.value)} placeholder="Ask for an approved operational fact" rows={3} /><button type="submit">Run authorized query</button></form>{structuredAnswer && <div className="answer-box"><p>{structuredAnswer.answer}</p><pre>{JSON.stringify(structuredAnswer.records, null, 2)}</pre></div>}</section><aside className="content-card audit-panel"><p className="eyebrow">Audit</p><h2>Visible activity</h2>{audit.length === 0 ? <p className="empty-state">No visible audit rows for this role.</p> : <ul>{audit.map((event) => <li key={event.event_id}><strong>{event.result_status}</strong><span>{event.query_category}</span><small>{new Date(event.occurred_at).toLocaleString()}</small></li>)}</ul>}</aside></section>
    </main>
  );
}

export default function App() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [timeline, setTimeline] = useState<CaseTimeline | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [policyQuestion, setPolicyQuestion] = useState("");
  const [policyAnswer, setPolicyAnswer] = useState<GroundedAnswer | null>(null);
  const [structuredQuestion, setStructuredQuestion] = useState("");
  const [structuredAnswer, setStructuredAnswer] = useState<StructuredAnswer | null>(null);
  const [messageBody, setMessageBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [denial, setDenial] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [localAuthAvailable, setLocalAuthAvailable] = useState(false);
  const [demoActive, setDemoActive] = useState(false);
  const [demoQuestion, setDemoQuestion] = useState("");
  const [demoAnswer, setDemoAnswer] = useState<DemoAnswer | null>(null);
  const [demoCases, setDemoCases] = useState<CaseRecord[]>([]);
  const [demoTimeline, setDemoTimeline] = useState<CaseTimeline | null>(null);
  const [demoCaseMessage, setDemoCaseMessage] = useState("");
  const accessToken = window.pmsAuth?.accessToken ?? "";

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
          setAuthenticated(true); setMe(identity); setDemoActive(true); setError(null);
          setDemoCases(await loadCaseQueue(""));
          setAuthChecked(true); setBusy(false);
          return;
        } catch (reason: unknown) {
          if (!(reason instanceof ApiError && reason.status === 401)) setError(errorMessage(reason));
        }
      }
      try {
        const identity = await loadMe(accessToken);
      setAuthenticated(true); setMe(identity); setError(null);
      if (identity.roles.includes("Tenant")) return;
      setCases(await loadCaseQueue(accessToken));
      try { setAudit(await loadAudit(accessToken)); } catch (reason: unknown) { if (!isDenied(reason)) setError(errorMessage(reason)); }
      } catch (reason: unknown) {
        if (reason instanceof ApiError && reason.status === 401) { setAuthenticated(false); setError(null); } else setError(errorMessage(reason));
      } finally { setAuthChecked(true); setBusy(false); }
    }
    void restoreSession();
  }, [accessToken]);

  async function selectCase(caseId: string) { try { setError(null); setTimeline(await loadTimeline(caseId, accessToken)); } catch (reason: unknown) { setError(errorMessage(reason)); } }
  async function sendMessage(event: FormEvent) { event.preventDefault(); if (!timeline || !messageBody.trim()) return; try { setBusy(true); await postCaseMessage(timeline.case.case_id, messageBody.trim(), accessToken); setMessageBody(""); setTimeline(await loadTimeline(timeline.case.case_id, accessToken)); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function submitPolicy(event: FormEvent) { event.preventDefault(); if (!policyQuestion.trim()) return; try { setBusy(true); setDenial(null); setPolicyAnswer(await runPolicyQuery(policyQuestion.trim(), accessToken)); } catch (reason: unknown) { setPolicyAnswer(null); if (isDenied(reason)) setDenial("Policy access denied by the server ACL."); else setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function submitStructured(event: FormEvent) { event.preventDefault(); if (!structuredQuestion.trim()) return; try { setBusy(true); setDenial(null); setStructuredAnswer(await runStructuredQuery(structuredQuestion.trim(), accessToken)); setAudit(await loadAudit(accessToken)); } catch (reason: unknown) { setStructuredAnswer(null); if (isDenied(reason)) { setDenial("Structured query denied; the denial is audit-recorded."); } else setError(errorMessage(reason)); } finally { setBusy(false); } }

  async function startDemo(identity: DemoIdentity) { try { setBusy(true); setError(null); const demoIdentity = await startDemoSession(identity); setMe(demoIdentity); setDemoCases(await loadCaseQueue("")); setDemoActive(true); setAuthenticated(true); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function startLocalLogin(username: string, password: string, role: LocalLoginRole) { try { setBusy(true); setError(null); const identity = await loginLocally(username, password, role); setMe(identity); setAuthenticated(true); setDemoActive(false); if (!identity.roles.includes("Tenant")) setCases(await loadCaseQueue("")); } catch (reason: unknown) { setAuthenticated(false); setMe(null); setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function exitDemo() { try { await endDemoSession(); } catch { /* The local session is removed from the UI even if the API has stopped. */ } setDemoActive(false); setAuthenticated(false); setMe(null); setDemoAnswer(null); setDemoCases([]); setDemoTimeline(null); setDemoCaseMessage(""); }
  async function refreshDemoTimeline(caseId: string) { const next = await loadTimeline(caseId, ""); setDemoTimeline(next); setDemoCases(await loadCaseQueue("")); }
  async function createDemoCase() { try { setBusy(true); setError(null); const item = await createCase({ title: "Review of approved lease and applicable land-policy provision", objective: "Controlled local review of approved lease summaries and the selected indexed land-management clarification.", initial_message: "Controlled local case created. No tenant, agreement, financial, or active-lease assertion is made.", unit_id: "land" }, ""); await refreshDemoTimeline(item.case_id); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function selectDemoCase(caseId: string) { try { setError(null); await refreshDemoTimeline(caseId); } catch (reason: unknown) { setError(errorMessage(reason)); } }
  async function submitDemo(event: FormEvent) { event.preventDefault(); if (!demoQuestion.trim()) return; try { setBusy(true); setError(null); const result = await runDemoQuery(demoQuestion.trim()); setDemoAnswer(result); if (demoTimeline && result.route !== "REQUEST_REFUSED" && !result.review_required) { const evidence = [ ...(result.structured ? [{ reference_type: "approved_query", reference_id: result.structured.query_id, version: result.structured.freshness_at }] : []), ...(result.document ? result.document.sources.map((source) => ({ reference_type: "document", reference_id: source.document_id, version: source.document_version_id })) : []) ]; await postCaseMessage(demoTimeline.case.case_id, `Controlled ${result.route} result: ${result.answer}`, "", evidence); await refreshDemoTimeline(demoTimeline.case.case_id); } } catch (reason: unknown) { setDemoAnswer(null); setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function sendDemoCaseMessage(event: FormEvent) { event.preventDefault(); if (!demoTimeline || !demoCaseMessage.trim()) return; try { setBusy(true); await postCaseMessage(demoTimeline.case.case_id, demoCaseMessage.trim(), ""); setDemoCaseMessage(""); await refreshDemoTimeline(demoTimeline.case.case_id); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function handoffToNo() { if (!demoTimeline) return; try { setBusy(true); await submitToNo(demoTimeline.case.case_id, ""); await refreshDemoTimeline(demoTimeline.case.case_id); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function verifyByNo() { if (!demoTimeline) return; try { setBusy(true); await verifyCase(demoTimeline.case.case_id, ""); await refreshDemoTimeline(demoTimeline.case.case_id); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }
  async function forwardToHod() { if (!demoTimeline) return; try { setBusy(true); await submitToHod(demoTimeline.case.case_id, ""); await refreshDemoTimeline(demoTimeline.case.case_id); } catch (reason: unknown) { setError(errorMessage(reason)); } finally { setBusy(false); } }

  if (!authChecked) return <main className="loading-page"><AppBrand /><p>Checking your protected PMS session…</p></main>;
  if (!authenticated || !me) return <LoginScreen error={error} demoAvailable={demoAvailable} localAuthAvailable={localAuthAvailable} startDemo={startDemo} startLocalLogin={startLocalLogin} />;
  if (demoActive) return <DemoWorkspace me={me} cases={demoCases} timeline={demoTimeline} question={demoQuestion} answer={demoAnswer} caseMessage={demoCaseMessage} busy={busy} error={error} setQuestion={setDemoQuestion} setCaseMessage={setDemoCaseMessage} submit={submitDemo} createDemoCase={createDemoCase} selectCase={selectDemoCase} sendCaseMessage={sendDemoCaseMessage} handoffToNo={handoffToNo} verifyByNo={verifyByNo} forwardToHod={forwardToHod} exit={exitDemo} />;
  const props: WorkspaceProps = { me, cases, timeline, audit, policyQuestion, policyAnswer, structuredQuestion, structuredAnswer, messageBody, busy, error, denial, setPolicyQuestion, setStructuredQuestion, setMessageBody, selectCase, submitPolicy, submitStructured, sendMessage };
  return isTenant(me) ? <TenantWorkspace {...props} /> : <StaffWorkspace {...props} />;
}
