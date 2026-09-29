"""Tests de la logique JS pure de feed.js sous Node (ignorés si Node est absent)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEED_JS = PROJECT_ROOT / "components" / "job_feed" / "feed.js"
NODE = shutil.which("node")
EXPORTS = (
    "\nexport { SEGMENTS, keyToCommand, excerpt, visibleJobs, pickAfterRemoval,"
    " moveSelection, actionsFor, safeUrl }\n"
)

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js absent")


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "feed_under_test.mjs").write_text(FEED_JS.read_text(encoding="utf-8") + EXPORTS, encoding="utf-8")
    check = tmp_path / "check.mjs"
    check.write_text(
        'import assert from "node:assert/strict"\n'
        'import * as feed from "./feed_under_test.mjs"\n' + body,
        encoding="utf-8",
    )
    return subprocess.run([NODE, str(check)], capture_output=True, text=True, timeout=30, encoding="utf-8")


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_syntaxe_et_chargement(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, "assert.equal(typeof feed.keyToCommand, 'function')"))


def test_raccourcis_clavier(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.equal(feed.keyToCommand('j'), 'next')
assert.equal(feed.keyToCommand('ArrowDown'), 'next')
assert.equal(feed.keyToCommand('k'), 'prev')
assert.equal(feed.keyToCommand('ArrowUp'), 'prev')
assert.equal(feed.keyToCommand('p'), 'applied')
assert.equal(feed.keyToCommand('x'), 'archive')
assert.equal(feed.keyToCommand('?'), 'help')
assert.equal(feed.keyToCommand('z'), null)
assert.equal(feed.keyToCommand('constructor'), null)
assert.equal(feed.keyToCommand('toString'), null)
"""))


def test_segments_et_filtre_traitees(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
const S = { new: 'NOUVEAU', applied: 'POSTULÉ', interview: 'ENTRETIEN', ignored: 'IGNORÉ' }
const jobs = [
  { id: 'a', align_tone: 'positive', reranked: true, status: 'NOUVEAU' },
  { id: 'b', align_tone: 'accent', reranked: true, status: 'NOUVEAU' },
  { id: 'c', align_tone: 'warn', reranked: false, status: 'POSTULÉ' },
]
const base = { segment: 'all', hideProcessed: false, overrides: {}, statuses: S }
const ids = (o) => feed.visibleJobs(jobs, { ...base, ...o }).map((j) => j.id)
assert.deepEqual(ids({}), ['a', 'b', 'c'])
assert.deepEqual(ids({ segment: 'core' }), ['a'])
assert.deepEqual(ids({ segment: 'good' }), ['b'])
assert.deepEqual(ids({ segment: 'unrated' }), ['c'])
assert.deepEqual(ids({ segment: 'inconnu' }), ['a', 'b', 'c'])
assert.deepEqual(ids({ hideProcessed: true }), ['a', 'b'])
// Un changement optimiste retire immédiatement l'offre quand les traitées sont masquées.
assert.deepEqual(ids({ hideProcessed: true, overrides: { a: 'POSTULÉ' } }), ['b'])
assert.deepEqual(ids({ hideProcessed: true, overrides: { c: 'NOUVEAU' } }), ['a', 'b', 'c'])
assert.deepEqual(feed.visibleJobs([], { ...base }), [])
"""))


def test_selection_apres_retrait_et_navigation(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.equal(feed.pickAfterRemoval([], 0), null)
assert.equal(feed.pickAfterRemoval(['a', 'b', 'c'], 1), 'b')
assert.equal(feed.pickAfterRemoval(['a', 'b'], 5), 'b')
assert.equal(feed.pickAfterRemoval(['a', 'b'], -1), 'a')
assert.equal(feed.moveSelection([], 'a', 1), null)
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'a', 1), 'b')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'c', 1), 'c')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'a', -1), 'a')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'absent', 1), 'a')
"""))


def test_actions_par_statut(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
const S = { new: 'NOUVEAU', applied: 'POSTULÉ', interview: 'ENTRETIEN', ignored: 'IGNORÉ' }
const labels = (status) => feed.actionsFor(status, S).map((a) => a.label)
assert.deepEqual(labels('NOUVEAU'), ['Marquer postulé', 'Archiver'])
assert.deepEqual(labels('POSTULÉ'), ['Entretien obtenu', 'Archiver'])
assert.deepEqual(labels('ENTRETIEN'), ['Rétablir au flux'])
assert.deepEqual(labels('IGNORÉ'), ['Rétablir au flux'])
// Statuts hors norme venus de la base : comportement du statut « nouveau ».
assert.deepEqual(labels('REJETÉ'), ['Marquer postulé', 'Archiver'])
assert.deepEqual(labels(undefined), ['Marquer postulé', 'Archiver'])
assert.equal(feed.actionsFor('POSTULÉ', S)[0].status, 'ENTRETIEN')
"""))


def test_extrait_et_url_sure(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.deepEqual(feed.excerpt('court', 420), { text: 'court', truncated: false })
const long = feed.excerpt('mot '.repeat(300), 50)
assert.equal(long.truncated, true)
assert.ok(long.text.endsWith(' …') && long.text.length <= 55, long.text)
assert.ok(!long.text.slice(0, -2).endsWith(' '), 'coupe sur un mot entier')
assert.equal(feed.safeUrl('https://a.example/x'), 'https://a.example/x')
assert.equal(feed.safeUrl('http://a.example'), 'http://a.example')
for (const bad of ['javascript:alert(1)', 'data:text/html,1', '//evil.example', '', null, undefined]) {
  assert.equal(feed.safeUrl(bad), '', String(bad))
}
"""))
