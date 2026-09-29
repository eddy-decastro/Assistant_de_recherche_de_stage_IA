// Composant « job_feed » : liste dense + panneau de détail (CCv2, JS vanilla, sans build).
// Toute donnée d'offre est rendue via createElement / textContent : jamais d'innerHTML.

const SEGMENTS = [
  { key: 'all', label: 'Tous', test: () => true },
  { key: 'core', label: 'Cœur de cible', test: (j) => j.align_tone === 'positive' },
  { key: 'good', label: 'Pertinent', test: (j) => j.align_tone === 'accent' },
  { key: 'unrated', label: 'Non évalué', test: (j) => !j.reranked },
]

const KEY_COMMANDS = {
  j: 'next', ArrowDown: 'next', k: 'prev', ArrowUp: 'prev',
  o: 'open', l: 'letter', p: 'applied', x: 'archive', '?': 'help', Escape: 'close',
}

const EXCERPT_LENGTH = 420
const TOAST_MS = 6000
const STORAGE_KEY = 'job_feed:selected'

// ---------- Logique pure (testée sous Node) ----------
function keyToCommand(key) {
  return Object.hasOwn(KEY_COMMANDS, key) ? KEY_COMMANDS[key] : null
}

function excerpt(text, length = EXCERPT_LENGTH) {
  if (text.length <= length) return { text, truncated: false }
  let cut = text.slice(0, length)
  const space = cut.lastIndexOf(' ')
  if (space > 0) cut = cut.slice(0, space)
  return { text: cut.replace(/[ ,;:.]+$/, '') + ' …', truncated: true }
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url || '') ? url : ''
}

function effectiveStatus(job, overrides) {
  return overrides[job.id] ?? job.status
}

function visibleJobs(jobs, options) {
  const { segment, hideProcessed, overrides, statuses } = options
  const seg = SEGMENTS.find((s) => s.key === segment) || SEGMENTS[0]
  return jobs.filter(
    (j) => seg.test(j) && (!hideProcessed || effectiveStatus(j, overrides) === statuses.new),
  )
}

function pickAfterRemoval(ids, removedIndex) {
  if (!ids.length) return null
  return ids[Math.min(Math.max(removedIndex, 0), ids.length - 1)]
}

function moveSelection(ids, currentId, delta) {
  if (!ids.length) return null
  const index = ids.indexOf(currentId)
  if (index === -1) return ids[0]
  return ids[Math.min(Math.max(index + delta, 0), ids.length - 1)]
}

function actionsFor(status, statuses) {
  if (status === statuses.applied) {
    return [
      { label: 'Entretien obtenu', status: statuses.interview },
      { label: 'Archiver', status: statuses.ignored },
    ]
  }
  if (status === statuses.interview || status === statuses.ignored) {
    return [{ label: 'Rétablir au flux', status: statuses.new }]
  }
  return [
    { label: 'Marquer postulé', status: statuses.applied },
    { label: 'Archiver', status: statuses.ignored },
  ]
}

// Réconcilie les changements optimistes avec la donnée serveur : un changement confirmé (ou dont
// l'offre a disparu des données) est abandonné ; un changement perdu (rerun fusionné par Streamlit)
// est renvoyé, un seul par mise à jour, avec un nombre d'essais borné.
function reconcileOverrides(overrides, jobs, attempts, maxAttempts = 2) {
  const byId = new Map(jobs.map((j) => [j.id, j]))
  const kept = {}
  const nextAttempts = {}
  let resend = null
  for (const [id, status] of Object.entries(overrides)) {
    const job = byId.get(id)
    if (!job || job.status === status) continue
    const tries = attempts[id] || 0
    if (tries >= maxAttempts) continue
    kept[id] = status
    if (resend === null) {
      resend = { id, status }
      nextAttempts[id] = tries + 1
    } else if (tries) {
      nextAttempts[id] = tries
    }
  }
  return { overrides: kept, attempts: nextAttempts, resend }
}

// L'annulation n'est proposée que vers un statut que le serveur accepte.
function canUndo(previous, statuses) {
  return Object.values(statuses).includes(previous)
}

// ---------- Rendu DOM ----------
function h(tag, props, ...kids) {
  const el = document.createElement(tag)
  for (const [name, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue
    if (name === 'class') el.className = value
    else if (name.startsWith('on')) el.addEventListener(name.slice(2), value)
    else el.setAttribute(name, value === true ? '' : String(value))
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)))
  }
  return el
}

const chip = (label, tone, extra) =>
  h('span', { class: `chip ${tone ? 'tone-' + tone : 'plain'} ${extra || ''}`.trim() }, label)

function sourceChip(job) {
  const dot = h('i')
  if (/^#[0-9a-f]{3,8}$/i.test(job.source_color || '')) dot.style.background = job.source_color
  return h('span', { class: 'chip plain' }, dot, job.source_label)
}

function shorten(text, max = 48) {
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

function section(title, ...content) {
  return h('div', { class: 'section' }, h('h3', {}, title), ...content)
}

function bulletList(items, kind) {
  return h('ul', { class: `list-plain ${kind}` }, items.map((item) => h('li', {}, item)))
}

function createInstance(root) {
  const $ = (selector) => root.querySelector(selector)
  const layoutEl = $('.layout')
  const listEl = $('.list')
  const countEl = $('.count')
  const segmentsEl = $('.segments')
  const detailEl = $('.detail-pane')
  const toastEl = $('.toast')
  const helpEl = $('.help')

  const inst = {
    data: { jobs: [], statuses: {}, status_labels: {}, hide_processed: false, grouped: false },
    selected: null,
    segment: 'all',
    overrides: {},
    mobileDetail: false,
    toastTimer: null,
    lastDetailId: null,
    pendingSelect: null,
    attempts: {},
    setTrigger: () => {},
  }
  try { inst.selected = sessionStorage.getItem(STORAGE_KEY) } catch (_) { /* stockage indisponible */ }

  const options = () => ({
    segment: inst.segment,
    hideProcessed: inst.data.hide_processed,
    overrides: inst.overrides,
    statuses: inst.data.statuses,
  })
  const visible = () => visibleJobs(inst.data.jobs, options())

  function render() {
    const list = visible()
    if (!list.some((j) => j.id === inst.selected)) inst.selected = list.length ? list[0].id : null
    if (inst.selected === null) inst.mobileDetail = false
    renderSegments()
    const rows = renderList(list)
    renderDetail(list)
    layoutEl.classList.toggle('show-detail', inst.mobileDetail)
    return rows
  }

  function renderSegments() {
    const base = visibleJobs(inst.data.jobs, { ...options(), segment: 'all' })
    countEl.textContent = `${base.length} offre${base.length > 1 ? 's' : ''}`
    segmentsEl.replaceChildren(
      ...SEGMENTS.map((seg) =>
        h(
          'button',
          {
            type: 'button',
            role: 'tab',
            class: 'seg' + (seg.key === inst.segment ? ' active' : ''),
            'aria-selected': String(seg.key === inst.segment),
            onclick: () => { inst.segment = seg.key; render() },
          },
          seg.label,
          h('span', { class: 'seg-n' }, String(base.filter(seg.test).length)),
        ),
      ),
    )
  }

  function renderList(list) {
    const top = listEl.scrollTop
    if (!list.length) {
      listEl.replaceChildren(h('li', { class: 'empty', role: 'presentation' }, 'Aucune offre ne correspond aux filtres courants.'))
      return new Map()
    }
    const rows = new Map()
    const nodes = []
    let lastGroup = null
    for (const job of list) {
      if (inst.data.grouped && job.group !== lastGroup) {
        lastGroup = job.group
        nodes.push(h('li', { class: 'group', role: 'presentation' }, job.group))
      }
      const status = effectiveStatus(job, inst.overrides)
      const selected = job.id === inst.selected
      const row = h(
        'li',
        {
          class: 'row' + (selected ? ' selected' : ''),
          role: 'option',
          'aria-selected': String(selected),
          onclick: () => select(job.id, true),
        },
        h('div', { class: `score tone-${job.align_tone}`, title: `${job.align_label} (${job.score_origin})` }, String(job.score)),
        h(
          'div',
          { class: 'row-main' },
          h('div', { class: 'row-title', title: job.title }, job.title),
          h('div', { class: 'row-meta' }, [job.company, job.location, job.date_label].filter(Boolean).join(' · ')),
        ),
        h(
          'div',
          { class: 'row-flags' },
          job.hard_cap ? chip('Plafonné', 'alert') : null,
          status === inst.data.statuses.new ? h('i', { class: 'dot-new', title: 'Nouveau' }) : null,
        ),
      )
      rows.set(job.id, row)
      nodes.push(row)
    }
    listEl.replaceChildren(...nodes)
    listEl.scrollTop = top
    return rows
  }

  function renderDetail(list) {
    const base = list.find((j) => j.id === inst.selected)
    if (!base) {
      detailEl.replaceChildren(h('div', { class: 'empty' }, 'Sélectionnez une offre pour afficher son évaluation.'))
      return
    }
    const S = inst.data.statuses
    const status = effectiveStatus(base, inst.overrides)
    const job = { ...base, status, status_label: inst.data.status_labels[status] || status }
    const url = safeUrl(job.url)
    const sameJob = inst.lastDetailId === job.id
    const scroll = detailEl.scrollTop

    const badges = [sourceChip(job), ...job.badges.map((b) => chip(b.label, b.tone))]
    if (job.verdict_label) badges.push(chip(job.verdict_label, job.verdict_tone))

    const parts = [
      h('button', { type: 'button', class: 'btn back', onclick: () => { inst.mobileDetail = false; render() } }, '← Retour à la liste'),
      h(
        'header',
        { class: 'detail-head' },
        h(
          'div',
          { class: 'detail-titles' },
          h('h2', { class: 'detail-title' }, job.title),
          h('div', { class: 'detail-meta' }, [job.company, job.location, job.date_label, job.status_label].filter(Boolean).join(' · ')),
        ),
        h(
          'div',
          { class: `score-box tone-${job.align_tone}`, title: `Score R&D (${job.score_origin})` },
          h('div', { class: 'score-big' }, h('b', {}, String(job.score)), h('span', {}, '/100')),
          h('div', { class: 'score-align' }, job.align_label),
          job.quality != null ? h('div', { class: 'score-quality' }, `qualité : ${Math.round(job.quality)}`) : null,
        ),
      ),
      h('div', { class: 'chips' }, badges),
      h(
        'div',
        { class: 'actions' },
        url ? h('a', { class: 'btn primary', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'Postuler ↗') : null,
        h('button', { type: 'button', class: 'btn', onclick: () => emitLetter(job) }, 'Lettre'),
        actionsFor(status, S).map((a) => h('button', { type: 'button', class: 'btn', onclick: () => changeStatus(job, a.status) }, a.label)),
      ),
    ]

    if (job.hard_cap) {
      parts.push(
        h('div', { class: 'alert tone-alert' }, h('b', {}, 'Verrou bloquant'), ` — ${job.hard_cap} : score plafonné, candidature à écarter ou à vérifier avant tout effort.`),
      )
    }
    if (job.rejection_reason) parts.push(section('Écartée par le filtre métier', h('p', { class: 'pre' }, job.rejection_reason)))

    if (job.sub_scores.length) {
      parts.push(
        section(
          "Grille d'évaluation",
          h(
            'div',
            { class: 'grid' },
            job.sub_scores.map((s) =>
              h(
                'div',
                { class: `sub tone-${s.tone}`, title: s.label },
                h('span', {}, s.short),
                h('span', { class: 'bar' }, [1, 2, 3, 4, 5].map((n) => h('i', { class: n <= s.value ? 'on' : '' }))),
                h('span', { class: 'sub-value' }, `${s.value}/5`),
              ),
            ),
          ),
        ),
      )
    }
    if (job.signals.length) {
      parts.push(
        section(
          'Signaux qualitatifs vérifiés',
          job.signals.map((s) => h('div', { class: 'signal' }, chip(s.label, s.tone), s.evidence ? h('em', {}, ` « ${s.evidence} »`) : null)),
        ),
      )
    }

    if (job.reranked) {
      parts.push(
        section(
          'Verdict du juge',
          job.strengths.length ? bulletList(job.strengths, 'pos') : h('p', { class: 'muted' }, '—'),
          h('h3', { style: 'margin-top:12px' }, "Points d'attention"),
          job.red_flags.length ? bulletList(job.red_flags, 'neg') : h('p', { class: 'muted' }, 'Aucun point de vigilance signalé.'),
        ),
      )
      if (job.reasoning) {
        parts.push(h('div', { class: 'section' }, h('details', {}, h('summary', {}, 'Analyse du juge (raisonnement)'), h('p', { class: 'pre' }, job.reasoning))))
      }
    } else {
      parts.push(
        section('Verdict du juge', h('p', { class: 'muted' }, 'Offre non évaluée à ce stade : lancez `python run_pipeline.py` pour déclencher le reranking.')),
      )
    }

    if (job.technologies.length) parts.push(section('Technologies détectées', h('div', { class: 'chips' }, job.technologies.map((t) => chip(t, null, 'tech')))))

    if (job.description) {
      const short = excerpt(job.description)
      parts.push(
        section(
          'Fiche de poste',
          h('p', { class: 'pre' }, short.text),
          short.truncated ? h('details', {}, h('summary', {}, 'Lire la fiche complète'), h('p', { class: 'pre' }, job.description)) : null,
        ),
      )
    } else {
      parts.push(section('Fiche de poste', h('p', { class: 'muted' }, "Fiche non fournie par la plateforme d'origine.")))
    }
    parts.push(h('p', { class: 'muted', style: 'margin-top:20px;font-size:12px' }, 'Raccourcis : j / k naviguer · o ouvrir · l lettre · p postulé · x archiver · ? aide'))

    detailEl.replaceChildren(...parts)
    detailEl.scrollTop = sameJob ? scroll : 0
    inst.lastDetailId = job.id
  }

  function select(id, openDetail) {
    inst.selected = id
    inst.pendingSelect = null
    try { sessionStorage.setItem(STORAGE_KEY, id) } catch (_) { /* stockage indisponible */ }
    if (openDetail) inst.mobileDetail = true
    const rows = render()
    const row = rows.get(id)
    if (row) row.scrollIntoView({ block: 'nearest' })
    root.focus({ preventScroll: true })
  }

  function emitLetter(job) {
    inst.setTrigger('action', { type: 'letter', id: job.id })
  }

  function changeStatus(job, status) {
    const previous = effectiveStatus(job, inst.overrides)
    if (previous === status) return
    const before = visible()
    const index = before.findIndex((j) => j.id === job.id)
    inst.overrides[job.id] = status
    const after = visible()
    if (!after.some((j) => j.id === job.id)) {
      inst.selected = pickAfterRemoval(after.map((j) => j.id), index)
      inst.mobileDetail = false
    }
    inst.setTrigger('action', { type: 'status', id: job.id, status })
    if (canUndo(previous, inst.data.statuses)) {
      showToast(`${inst.data.status_labels[status] || status} : ${shorten(job.title)}`, () => undo(job.id, previous))
    } else {
      hideToast()
    }
    render()
    root.focus({ preventScroll: true })
  }

  function undo(id, previous) {
    inst.overrides[id] = previous
    inst.selected = id
    inst.pendingSelect = id // l'offre peut être absente des données jusqu'au prochain rerun
    inst.setTrigger('action', { type: 'status', id, status: previous })
    hideToast()
    render()
    root.focus({ preventScroll: true })
  }

  function showToast(message, onUndo) {
    clearTimeout(inst.toastTimer)
    toastEl.replaceChildren(h('span', {}, message), h('button', { type: 'button', class: 'toast-undo', onclick: onUndo }, 'Annuler'))
    toastEl.hidden = false
    inst.toastTimer = setTimeout(hideToast, TOAST_MS)
  }

  function hideToast() {
    toastEl.hidden = true
    toastEl.replaceChildren()
  }

  root.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || '')) return
    const command = keyToCommand(event.key)
    if (!command) return
    const list = visible()
    const current = list.find((j) => j.id === inst.selected)
    const S = inst.data.statuses
    switch (command) {
      case 'next':
      case 'prev':
        event.preventDefault()
        select(moveSelection(list.map((j) => j.id), inst.selected, command === 'next' ? 1 : -1), false)
        break
      case 'open':
        if (current && safeUrl(current.url)) window.open(safeUrl(current.url), '_blank', 'noopener,noreferrer')
        break
      case 'letter':
        if (current) emitLetter(current)
        break
      case 'applied':
        if (current) changeStatus(current, S.applied)
        break
      case 'archive':
        if (current) changeStatus(current, S.ignored)
        break
      case 'help':
        helpEl.hidden = !helpEl.hidden
        break
      case 'close':
        helpEl.hidden = true
        if (inst.mobileDetail) { inst.mobileDetail = false; render() }
        break
    }
  })

  inst.update = (data, setTrigger) => {
    inst.data = data
    inst.setTrigger = setTrigger
    const settled = reconcileOverrides(inst.overrides, data.jobs, inst.attempts)
    inst.overrides = settled.overrides
    inst.attempts = settled.attempts
    if (settled.resend) setTrigger('action', { type: 'status', ...settled.resend })
    let restored = false
    if (inst.pendingSelect && data.jobs.some((j) => j.id === inst.pendingSelect)) {
      inst.selected = inst.pendingSelect
      inst.pendingSelect = null
      restored = true
    }
    const rows = render()
    if (restored && rows.get(inst.selected)) rows.get(inst.selected).scrollIntoView({ block: 'nearest' })
  }
  return inst
}

const instances = new WeakMap()

export default function (component) {
  const { parentElement, data, setTriggerValue } = component
  let inst = instances.get(parentElement)
  if (!inst) {
    const root = parentElement.querySelector('.root')
    if (!root) return
    inst = createInstance(root)
    instances.set(parentElement, inst)
  }
  inst.update(data, setTriggerValue)
}
