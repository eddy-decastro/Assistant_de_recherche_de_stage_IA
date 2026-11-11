"use client";

// Page /file : file d'attente des candidatures, lue et manipulée par Claude in Chrome.
// Une seule offre courante, chaque valeur en texte visible + bouton Copier, puis un
// compte rendu caveman (2 phrases, 300 caractères) et « Marquer postulé » / « Échec ».

import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { QueueCandidate, QueueItem, QueueItemFull, QueueOutcome, QueueResponse, QueueState } from "@/lib/types";
import styles from "./file.module.css";
import { REPORT_MAX_CHARS, REPORT_MAX_SENTENCES, cleanReport, copyText, countSentences, reportError } from "./report";

const STATE_LABELS: Record<QueueState, string> = {
  pending: "En attente",
  failed: "Échec",
  applied: "Postulé",
};

const CANDIDATE_FIELDS: { key: keyof QueueCandidate; label: string }[] = [
  { key: "first_name", label: "Prénom" },
  { key: "last_name", label: "Nom" },
  { key: "full_name", label: "Nom complet" },
  { key: "email", label: "E-mail" },
  { key: "phone", label: "Téléphone" },
  { key: "city", label: "Ville" },
  { key: "linkedin", label: "LinkedIn" },
  { key: "github", label: "GitHub" },
  { key: "school", label: "École" },
  { key: "degree_level", label: "Niveau d'études" },
  { key: "internship_duration", label: "Durée du stage" },
  { key: "start_date", label: "Date de début" },
  { key: "contract_type", label: "Type de contrat" },
  { key: "work_authorization", label: "Autorisation de travail" },
];

/** Bouton « Copier » avec retour visuel ; le libellé accessible nomme le champ copié. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  if (!value) return null;
  return (
    <button
      type="button"
      className="btn"
      aria-label={`Copier ${label}`}
      onClick={async () => {
        const ok = await copyText(value);
        setDone(ok);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? "Copié" : "Copier"}
    </button>
  );
}

function CurrentOffer({
  item,
  onReport,
}: {
  item: QueueItemFull;
  onReport: (outcome: QueueOutcome, report: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState<QueueOutcome | null>(null);

  // Nouveau brouillon à chaque nouvelle offre courante (pas à chaque rafraîchissement).
  useEffect(() => {
    setDraft("");
    setTouched(false);
  }, [item.job_id]);

  const cleaned = cleanReport(draft);
  const sentences = cleaned ? countSentences(cleaned) : 0;
  const error = reportError(draft);
  const tooLong = cleaned.length > REPORT_MAX_CHARS;
  const tooMany = sentences > REPORT_MAX_SENTENCES;

  const submit = async (outcome: QueueOutcome) => {
    setTouched(true);
    if (error || busy) return;
    setBusy(outcome);
    const ok = await onReport(outcome, cleaned);
    setBusy(null);
    if (ok) {
      setDraft("");
      setTouched(false);
    }
  };

  const answers = Object.entries(item.answers || {}).filter(([, v]) => v);
  const cvPdf = item.files?.cv_pdf || item.candidate?.cv_pdf_path || "";
  const letterPdf = item.files?.cover_letter_pdf || "";

  return (
    <>
      <article className={`${styles.card} ${styles.current}`} aria-labelledby="offre-courante-titre">
        <p className={styles.eyebrow}>Offre courante · {item.remaining} offre{item.remaining > 1 ? "s" : ""} en attente, celle-ci comprise</p>
        <h2 id="offre-courante-titre">
          {item.company || "Entreprise inconnue"} : {item.title || "Intitulé inconnu"}
        </h2>
        <dl className={styles.facts}>
          <dt>Entreprise</dt>
          <dd id="offre-entreprise">{item.company || "Non renseignée"}</dd>
          <dt>Intitulé</dt>
          <dd id="offre-intitule">{item.title || "Non renseigné"}</dd>
          {item.location && (
            <>
              <dt>Lieu</dt>
              <dd>{item.location}</dd>
            </>
          )}
          <dt>URL de l&apos;offre</dt>
          <dd>
            {item.url ? (
              <a id="offre-url" className={styles.offerUrl} href={item.url} target="_blank" rel="noopener noreferrer">
                {item.url}
              </a>
            ) : (
              "Aucune URL"
            )}
          </dd>
          <dt>Méthode de candidature</dt>
          <dd>
            <strong>{item.method_label || item.method || "Inconnue"}</strong>
            {item.needs_account && (
              <span className={`${styles.badge} tone-alert`}>Compte requis</span>
            )}
            {item.method_hint && <div className="muted small">{item.method_hint}</div>}
          </dd>
          {item.contact_email && (
            <>
              <dt>E-mail de contact</dt>
              <dd>
                {item.contact_email} <CopyButton value={item.contact_email} label="l'e-mail de contact" />
              </dd>
            </>
          )}
          <dt>Envoi final</dt>
          <dd>
            {item.auto_submit
              ? "Autorisé : cliquer Envoyer seulement si rien d'obligatoire ne manque."
              : "Interdit : ne pas cliquer Envoyer / Submit, Eddy validera."}
          </dd>
        </dl>

        {item.needs_account && (
          <div className={`${styles.callout} tone-alert`} role="note">
            <strong>Compte requis</strong>
            <span>
              Ne crée aucun compte et ne saisis aucun mot de passe. Écris « Échec : compte requis. » dans le compte
              rendu puis clique « Échec ».
            </span>
          </div>
        )}

        {item.instructions?.length > 0 && (
          <section aria-labelledby="consignes-titre">
            <h3 id="consignes-titre">Consignes pour cette offre</h3>
            <ol className={styles.instructions}>
              {item.instructions.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ol>
          </section>
        )}
      </article>

      <section className={styles.card} aria-labelledby="pdf-titre">
        <h2 id="pdf-titre">Fichiers PDF : à joindre à la main par Eddy</h2>
        <div className={`${styles.callout} tone-warn`} role="note">
          <strong>CV à joindre par Eddy</strong>
          <span>
            Claude ne peut pas joindre de PDF. Si le formulaire exige un fichier, ne soumets pas : laisse l&apos;onglet
            ouvert et écris « CV à joindre » dans le compte rendu.
          </span>
        </div>
        <table className={styles.fields}>
          <tbody>
            <tr>
              <th scope="row">CV (PDF)</th>
              <td className={styles.path}>{cvPdf || "Aucun CV PDF configuré (candidate.cv_pdf_path dans config.yaml)"}</td>
              <td>
                <CopyButton value={cvPdf} label="le chemin du CV" />
              </td>
            </tr>
            <tr>
              <th scope="row">Lettre de motivation (PDF)</th>
              <td className={styles.path}>{letterPdf || "Aucune lettre PDF générée"}</td>
              <td>
                <CopyButton value={letterPdf} label="le chemin de la lettre PDF" />
              </td>
            </tr>
            {item.files?.offer_dir && (
              <tr>
                <th scope="row">Dossier de l&apos;offre</th>
                <td className={styles.path}>{item.files.offer_dir}</td>
                <td>
                  <CopyButton value={item.files.offer_dir} label="le chemin du dossier" />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className={styles.card} aria-labelledby="candidat-titre">
        <h2 id="candidat-titre">Infos candidat</h2>
        <table className={styles.fields}>
          <tbody>
            {CANDIDATE_FIELDS.map(({ key, label }) => {
              const value = item.candidate?.[key] || "";
              return (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  <td id={`candidat-${key}`}>{value || <span className="muted">Non renseigné</span>}</td>
                  <td>
                    <CopyButton value={value} label={label} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className={styles.card} aria-labelledby="accroche-titre">
        <div className={styles.blockHead}>
          <h2 id="accroche-titre">Accroche courte (300 caractères max)</h2>
          <CopyButton value={item.short_pitch} label="l'accroche courte" />
        </div>
        <p className={styles.textBlock}>{item.short_pitch || "Aucune accroche."}</p>
        {item.short_motivation && (
          <>
            <div className={styles.blockHead}>
              <h3>Motivation courte (champ « Pourquoi nous ? »)</h3>
              <CopyButton value={item.short_motivation} label="la motivation courte" />
            </div>
            <p className={styles.textBlock}>{item.short_motivation}</p>
          </>
        )}
      </section>

      <section className={styles.card} aria-labelledby="lettre-titre">
        <div className={styles.blockHead}>
          <h2 id="lettre-titre">Lettre de motivation (texte à coller)</h2>
          <CopyButton value={item.cover_letter} label="la lettre de motivation" />
        </div>
        {item.cover_letter ? (
          // Texte visible (pas un <textarea>) : la lecture du texte de la page par
          // Claude in Chrome ne voit pas le contenu d'un champ de saisie.
          <p className={styles.textBlock} id="lettre-texte" aria-labelledby="lettre-titre">
            {item.cover_letter}
          </p>
        ) : (
          <p className="muted">Aucune lettre pour cette offre.</p>
        )}
      </section>

      {answers.length > 0 && (
        <section className={styles.card} aria-labelledby="reponses-titre">
          <h2 id="reponses-titre">Réponses types</h2>
          <table className={styles.fields}>
            <tbody>
              {answers.map(([question, answer]) => (
                <tr key={question}>
                  <th scope="row">{question}</th>
                  <td style={{ whiteSpace: "pre-wrap" }}>{answer}</td>
                  <td>
                    <CopyButton value={answer} label={`la réponse « ${question} »`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className={styles.card} aria-labelledby="compte-rendu-titre">
        <h2 id="compte-rendu-titre">Compte rendu de la candidature</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit("applied");
          }}
          style={{ display: "grid", gap: 10 }}
        >
          <label className="field-label" htmlFor="compte-rendu">
            <span style={{ color: "var(--ink)", fontWeight: 600, fontSize: 14 }}>
              Compte rendu (2 phrases, style caveman)
            </span>
            <span>
              Ex. « Moi remplir formulaire Thales. CV pas mis, Eddy mettre CV puis cliquer Envoyer. » Pour un échec, écris
              la raison (ex. « Échec : compte requis. »).
            </span>
          </label>
          <textarea
            id="compte-rendu"
            name="compte-rendu"
            className={`field ${styles.report}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => setTouched(true)}
            aria-describedby="compte-rendu-compteur compte-rendu-erreur"
            aria-invalid={touched && !!error}
            placeholder="Moi remplir formulaire. Eddy cliquer Envoyer."
          />
          <p id="compte-rendu-compteur" className={styles.counter} aria-live="polite">
            <span className={tooLong ? styles.counterBad : undefined}>
              {cleaned.length} / {REPORT_MAX_CHARS} caractères
            </span>
            {" · "}
            <span className={tooMany ? styles.counterBad : undefined}>
              {sentences} / {REPORT_MAX_SENTENCES} phrases
            </span>
          </p>
          <p id="compte-rendu-erreur" className="error-text" role={touched && error ? "alert" : undefined}>
            {touched && error ? error : ""}
          </p>
          <div className={styles.bigActions}>
            <button
              type="submit"
              className={`btn primary ${styles.bigBtn}`}
              disabled={busy !== null}
              aria-label="Marquer postulé"
            >
              {busy === "applied" ? "Enregistrement…" : "Marquer postulé"}
            </button>
            <button
              type="button"
              className={`btn ${styles.bigBtn} ${styles.failBtn}`}
              disabled={busy !== null}
              aria-label="Échec"
              onClick={() => void submit("failed")}
            >
              {busy === "failed" ? "Enregistrement…" : "Échec"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}

function QueueList({ queue, currentId }: { queue: QueueResponse | null; currentId: string | null }) {
  const items: QueueItem[] = queue?.items ?? [];
  return (
    <section className={styles.card} aria-labelledby="liste-titre">
      <h2 id="liste-titre">Liste de la file</h2>
      <p className="muted small">Ordre de passage : offres sans compte d&apos;abord, puis ordre d&apos;ajout.</p>
      {items.length === 0 ? (
        <p className="muted">Aucune offre dans la file.</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.queueTable}>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Entreprise</th>
                <th scope="col">Intitulé</th>
                <th scope="col">Méthode</th>
                <th scope="col">État</th>
                <th scope="col">Compte rendu</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={it.job_id} aria-current={it.job_id === currentId ? "true" : undefined}>
                  <td>{i + 1}</td>
                  <td>{it.company}</td>
                  <td>
                    {it.url ? (
                      <a className="link" href={it.url} target="_blank" rel="noopener noreferrer">
                        {it.title}
                      </a>
                    ) : (
                      it.title
                    )}
                  </td>
                  <td>
                    {it.method_label || it.method}
                    {it.needs_account && <span className={`${styles.badge} tone-alert`}>Compte requis</span>}
                  </td>
                  <td>
                    {it.job_id === currentId ? "Offre courante" : STATE_LABELS[it.state] ?? it.state}
                    {it.to_finish && <span className={`${styles.badge} tone-warn`}>À finir par Eddy</span>}
                  </td>
                  <td>{it.report || <span className="muted">Aucun</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function QueuePage() {
  const [current, setCurrent] = useState<QueueItemFull | null>(null);
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [cur, list] = await Promise.all([api.queueCurrent(), api.queue(true)]);
      setCurrent(cur.current);
      setQueue(list);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
    // Claude revient sur cet onglet après chaque formulaire : on relit la file.
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  const onReport = useCallback(
    async (outcome: QueueOutcome, report: string) => {
      if (!current) return false;
      const done = current;
      try {
        await api.queueReport(done.job_id, outcome, report);
        setNotice(
          `${outcome === "applied" ? "Postulé" : "Échec"} enregistré pour ${done.company || done.title}. Offre suivante affichée.`,
        );
        setError("");
        await load();
        window.scrollTo({ top: 0 });
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    [current, load],
  );

  const counts = queue?.counts;

  return (
    <main className={styles.page}>
      <header className={styles.top}>
        <div>
          <h1>File de candidature Claude in Chrome</h1>
          {counts && (
            <ul className={styles.counts} aria-label="Compteurs de la file">
              <li>En attente : {counts.pending}</li>
              <li>Échec : {counts.failed}</li>
              <li>Postulé : {counts.applied}</li>
            </ul>
          )}
        </div>
        <nav className={styles.topLinks} aria-label="Navigation">
          <button type="button" className="btn" onClick={() => void load()}>
            Actualiser la file
          </button>
          <Link className="btn ghost" href="/">
            Retour au tri des offres
          </Link>
        </nav>
      </header>

      {notice && (
        <p className={styles.success} role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {!loaded ? (
        <p className="muted" role="status">
          Chargement de la file…
        </p>
      ) : current ? (
        <CurrentOffer item={current} onReport={onReport} />
      ) : (
        !error && (
          <section className={`${styles.card} ${styles.empty}`} aria-labelledby="file-vide-titre">
            <h2 id="file-vide-titre">File vide</h2>
            <p>Aucune offre en attente. Le travail est terminé : arrête-toi ici.</p>
            <p className="muted small">
              Pour remplir la file, sélectionne des offres dans le tri puis clique « Pack Claude ».
            </p>
          </section>
        )
      )}

      {loaded && <QueueList queue={queue} currentId={current?.job_id ?? null} />}
    </main>
  );
}
