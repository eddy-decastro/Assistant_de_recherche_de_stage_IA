import { formatDate } from "@/lib/format";
import type { ApplicationReport } from "@/lib/types";

const STATE_LABELS: Record<ApplicationReport["state"], { label: string; tone: string }> = {
  pending: { label: "En attente dans la file", tone: "mute" },
  applied: { label: "Postulé par Claude", tone: "positive" },
  failed: { label: "Échec", tone: "alert" },
};

// Compte rendu de candidature (file Claude in Chrome) : état, méthode, et les 2 phrases de Claude.
export function ApplicationReportLine({ report, compact = false }: { report: ApplicationReport; compact?: boolean }) {
  const st = STATE_LABELS[report.state] ?? { label: report.state, tone: "mute" };
  const when = formatDate(report.updated_at);
  return (
    <div className={`app-report ${compact ? "compact" : ""}`}>
      <p className="app-report-head">
        <span className={`tag small-tag tone-${st.tone}`}>{st.label}</span>
        {report.to_finish && <span className="tag small-tag tone-warn">À finir par toi</span>}
        {!compact && report.method_label && (
          <span className="muted small">
            {report.method_label}
            {report.needs_account ? " · compte requis" : ""}
            {when ? ` · ${when}` : ""}
          </span>
        )}
      </p>
      {report.report ? (
        <p className="app-report-text">{report.report}</p>
      ) : (
        !compact && <p className="muted small">Pas encore de compte rendu.</p>
      )}
    </div>
  );
}
