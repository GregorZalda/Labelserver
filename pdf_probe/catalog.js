(function () {
  let token = null;
  let generation = 0;
  const drafts = {};
  const entriesEl = document.getElementById('entries');
  const statusEl = document.getElementById('status');
  const emptyEl = document.getElementById('empty');

  function setStatus(text) { statusEl.textContent = text; }

  function stateLabel(state) {
    return { proposed: 'Vorschlag', approved: 'Bestätigt', ignored: 'Kein Symbol' }[state] || state;
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.method === 'POST') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-Catalog-Token'] = token;
    }
    return fetch(path, opts).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (d) {
          const err = new Error((d && d.error) || ('HTTP ' + res.status));
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    });
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function renderEntry(entry) {
    const card = el('div', 'card entry');
    const head = el('div', 'entry-head');

    const img = document.createElement('img');
    img.src = '/api/entries/' + entry.key + '/image';
    img.alt = 'Ausschnitt';
    img.className = 'crop';
    head.appendChild(img);

    const info = el('div', 'entry-info');
    const shown = entry.confirmed_name || entry.suggested_name || '(ohne Vorschlag)';
    info.appendChild(el('h3', null, shown));
    info.appendChild(el('p', 'muted',
      'Vorschlag: ' + (entry.suggested_name || '–') +
      ' · Quelle: ' + (entry.suggestion_source || '–') +
      ' · Art: ' + (entry.suggestion_kind || '–')));
    if (entry.observed_text) {
      info.appendChild(el('p', 'muted mono', 'OCR-Beobachtung: ' + entry.observed_text));
    }
    const stateBadge = el('span', 'badge ' +
      (entry.state === 'approved' ? 'ok' : entry.state === 'ignored' ? 'unknown' : 'cand'),
      stateLabel(entry.state));
    info.appendChild(stateBadge);
    if (entry.occurrence_count) {
      info.appendChild(el('span', 'muted', ' · ' + entry.occurrence_count + ' Vorkommen'));
    }
    head.appendChild(info);
    card.appendChild(head);

    const form = el('div', 'entry-form');
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 200;
    input.value = drafts[entry.key] !== undefined
      ? drafts[entry.key]
      : (entry.confirmed_name || entry.suggested_name || '');
    input.setAttribute('aria-label', 'Name');
    input.addEventListener('input', function () { drafts[entry.key] = input.value; });
    form.appendChild(input);

    const save = el('button', 'primary', 'Benennung bestätigen');
    const ignore = el('button', null, 'Kein Symbol');
    const unconfirm = el('button', null, 'Bestätigung aufheben');
    const ocr = el('button', null, 'Ausschnitt-OCR');
    form.appendChild(save); form.appendChild(ignore);
    form.appendChild(unconfirm); form.appendChild(ocr);
    card.appendChild(form);

    const msg = el('p', 'muted entry-msg', '');
    card.appendChild(msg);

    function post(body) {
      const controls = card.querySelectorAll('input, button');
      controls.forEach(function (c) { c.disabled = true; });
      msg.textContent = 'Speichere …';
      return api('/api/entries/' + entry.key, {
        method: 'POST',
        body: JSON.stringify(body),
      }).then(function () {
        delete drafts[entry.key];
        return load();
      }).then(function () {
        setStatus('Gespeichert: ' + (input.value || '(kein Name)'));
      }).catch(function (err) {
        controls.forEach(function (c) { c.disabled = false; });
        msg.textContent = 'Fehler: ' + err.message + ' (bitte neu laden)';
        if (err.status === 409) load();
      });
    }

    save.addEventListener('click', function () {
      post({ name: input.value, state: 'approved', version: entry.version });
    });
    ignore.addEventListener('click', function () {
      post({ name: input.value || null, state: 'ignored', version: entry.version });
    });
    unconfirm.addEventListener('click', function () {
      post({ name: input.value || null, state: 'proposed', version: entry.version });
    });
    ocr.addEventListener('click', function () {
      msg.textContent = 'OCR-Job gestartet …';
      api('/api/entries/' + entry.key + '/ocr', {
        method: 'POST', body: JSON.stringify({}),
      }).then(function (d) { pollJob(d.job, msg); })
        .catch(function (err) { msg.textContent = 'OCR-Fehler: ' + err.message; });
    });

    if (entry.occurrences && entry.occurrences.length) {
      const occ = el('details', 'occ');
      occ.appendChild(el('summary', null, 'Vorkommen (' + entry.occurrences.length + ')'));
      const ul = el('ul');
      entry.occurrences.forEach(function (o) {
        const li = el('li');
        const link = document.createElement('a');
        link.href = '/reports/' + o.document_id + '/';
        link.textContent = o.source_name;
        link.target = '_blank';
        li.appendChild(link);
        li.appendChild(document.createTextNode(' — ' + o.template_id));
        ul.appendChild(li);
      });
      occ.appendChild(ul);
      card.appendChild(occ);
    }
    return card;
  }

  function pollJob(jobId, msgEl) {
    const timer = setInterval(function () {
      api('/api/jobs/' + jobId).then(function (d) {
        const j = d.job;
        if (j.state === 'done') {
          clearInterval(timer);
          load().then(function () {
            setStatus('OCR abgeschlossen — Vorschlag aktualisiert.');
          });
        } else if (j.state === 'error') {
          clearInterval(timer);
          msgEl.textContent = 'OCR fehlgeschlagen: ' + (j.error || '');
        }
      }).catch(function () { clearInterval(timer); });
    }, 1200);
  }

  function load() {
    const gen = ++generation;
    const state = document.getElementById('state').value;
    const search = document.getElementById('search').value;
    let url = '/api/entries?';
    if (state) url += 'state=' + encodeURIComponent(state) + '&';
    if (search) url += 'search=' + encodeURIComponent(search);
    return api(url).then(function (d) {
      return Promise.all(d.entries.map(function (e) {
        return api('/api/entries/' + e.key).then(function (r) { return r.entry; });
      }));
    }).then(function (entries) {
      if (gen !== generation) return;
      entriesEl.replaceChildren();
      emptyEl.hidden = entries.length > 0;
      entries.forEach(function (e) { entriesEl.appendChild(renderEntry(e)); });
      setStatus(entries.length + ' Einträge');
    }).catch(function (err) { setStatus('Ladefehler: ' + err.message); });
  }

  document.getElementById('apply').addEventListener('click', load);
  document.getElementById('search').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') load();
  });
  document.getElementById('import').addEventListener('click', function () {
    setStatus('Import läuft …');
    api('/api/import', { method: 'POST', body: JSON.stringify({}) })
      .then(function (d) {
        const summary = d.summary;
        return load().then(function () {
          let text = 'Import: ' + summary.imported + '/' + summary.files +
            ' Dateien, ' + summary.skipped.length + ' übersprungen.';
          const blocked = summary.legacy_blocked || [];
          if (blocked.length) {
            text += ' ' + blocked.length +
              ' Bericht(e) mit alten Vorlagentreffern: nur Dokument registriert, ' +
              'Symboltreffer nicht übernommen.';
          }
          setStatus(text);
        });
      })
      .catch(function (err) { setStatus('Import-Fehler: ' + err.message); });
  });

  api('/api/bootstrap').then(function (d) {
    token = d.csrf;
    load();
  }).catch(function () { setStatus('Bootstrap fehlgeschlagen.'); });
})();
