(() => {
	const basePath = (window.APP_CONFIG && window.APP_CONFIG.basePath) || '';
	const $ = (id) => document.getElementById(id);

	function formatDate(s) {
		if (!s) return '—';
		try {
			const d = new Date(s);
			return d.toLocaleString();
		} catch { return s; }
	}
	function fmtIntSecToHhMm(sec) {
		if (!sec || sec < 0) return '—';
		const h = Math.floor(sec/3600);
		const m = Math.floor((sec%3600)/60);
		return `${h}h ${m}m`;
	}
	function fmtMinutes(mins) {
		if (!mins || mins <= 0) return '—';
		const h = Math.floor(mins/60);
		const m = mins % 60;
		if (h && m) return `${h}h ${m}m`;
		if (h) return `${h}h`;
		return `${m}m`;
	}
	function appendDotAndText(el, text) {
		const dot = document.createElement('span');
		dot.className = 'dot';
		el.appendChild(dot);
		el.appendChild(document.createTextNode(text));
	}

	function render(data) {
		const occupied = !!data.occupied;
		const pill = $('status-pill');
		const txt = $('status-text');
		pill.classList.toggle('ok', !occupied);
		pill.classList.toggle('bad', occupied);
		txt.textContent = occupied ? 'Occupied' : 'Free';

		$('sess-user').textContent = data.user || '—';
		$('sess-start').textContent = formatDate(data.start);
		$('sess-target').textContent = data.target || '—';
		$('sess-planned').textContent = formatDate(data.planned_end);
		$('sess-hb').textContent = formatDate(data.last_heartbeat);
		$('last-refresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();

		const hosts = data.hosts || {};
		const list = $('hosts-list');
		const empty = $('hosts-empty');
		list.innerHTML = '';
		const entries = Object.values(hosts);
		if (entries.length === 0) {
			empty.style.display = '';
		} else {
			empty.style.display = 'none';
			entries.forEach(h => {
				const li = document.createElement('li');
				const row = document.createElement('div');
				row.className = 'host-row';
				const idSpan = document.createElement('div');
				idSpan.className = 'host-id';
				idSpan.textContent = h.hostId || 'host';
				const chips = document.createElement('div');
				chips.className = 'chips';

				// severity helpers
				const now = Date.now();
				const lastMs = h.ts ? (new Date(h.ts)).getTime() : 0;
				const deltaSec = lastMs ? Math.max(0, Math.round((now - lastMs)/1000)) : null;
				const sev = (val, goodLow, warnHigh, invert=false) => {
					if (val === null || val === undefined) return 'warn';
					let v = Number(val);
					if (isNaN(v)) return 'warn';
					if (invert) v = 100 - v; // interpret "free %" as inverse load
					if (v <= goodLow) return 'ok';
					if (v <= warnHigh) return 'warn';
					return 'bad';
				};

				// Last seen
				const lastChip = document.createElement('span');
				lastChip.className = 'chip ' + (deltaSec === null ? 'warn' :
					(deltaSec <= 120 ? 'ok' : (deltaSec <= 300 ? 'warn' : 'bad')));
				appendDotAndText(lastChip, `Last: ${formatDate(h.ts)}`);
				chips.appendChild(lastChip);

				// OS
				const osChip = document.createElement('span');
				osChip.className = 'chip mono';
				osChip.textContent = `OS: ${h.osVersion || '—'}`;
				chips.appendChild(osChip);

				// Uptime
				const upChip = document.createElement('span');
				upChip.className = 'chip mono';
				upChip.textContent = `Uptime: ${fmtIntSecToHhMm(h.uptimeSec)}`;
				chips.appendChild(upChip);

				// CPU
				const cpu = Math.round(h.cpuPercent || 0);
				const cpuChip = document.createElement('span');
				cpuChip.className = 'chip ' + sev(cpu, 50, 80, false);
				appendDotAndText(cpuChip, `CPU: ${cpu}%`);
				chips.appendChild(cpuChip);

				// RAM
				const ram = Math.round(h.memPercent || 0);
				const ramChip = document.createElement('span');
				ramChip.className = 'chip ' + sev(ram, 50, 80, false);
				appendDotAndText(ramChip, `RAM: ${ram}%`);
				chips.appendChild(ramChip);

				// Disk C free (invert severity: low free -> worse)
				const cfree = Math.round(h.diskCPercent || 0);
				const diskChip = document.createElement('span');
				// good when free >= 30% (invert => load <= 70), warn 15-30, bad < 15
				// we implement via thresholds on free directly
				const diskSev = (val) => {
					if (val === null || val === undefined) return 'warn';
					const v = Number(val);
					if (isNaN(v)) return 'warn';
					if (v >= 30) return 'ok';
					if (v >= 15) return 'warn';
					return 'bad';
				};
				diskChip.className = 'chip ' + diskSev(cfree);
				appendDotAndText(diskChip, `C free: ${cfree}%`);
				chips.appendChild(diskChip);

				row.appendChild(idSpan);
				row.appendChild(chips);
				li.appendChild(row);
				list.appendChild(li);
			});
		}

		// Telescope chips (Observed Region)
		const tel = data.telescope || {};
		const telList = $('tel-list');
		const telEmpty = $('tel-empty');
		telList.innerHTML = '';
		const telEntries = Object.values(tel);
		if (telEntries.length === 0) {
			telEmpty.style.display = '';
		} else {
			telEmpty.style.display = 'none';
			telEntries.forEach(t => {
				const li = document.createElement('li');
				const row = document.createElement('div');
				row.className = 'host-row';
				const idSpan = document.createElement('div');
				idSpan.className = 'host-id';
				idSpan.textContent = t.hostId || 'telescope';
				const chips = document.createElement('div');
				chips.className = 'chips';

				const lastChip = document.createElement('span');
				const lastMs = t.ts ? (new Date(t.ts)).getTime() : 0;
				const now = Date.now();
				const deltaSec = lastMs ? Math.max(0, Math.round((now - lastMs)/1000)) : null;
				lastChip.className = 'chip ' + (deltaSec === null ? 'warn' :
					(deltaSec <= 1200 ? 'ok' : (deltaSec <= 1800 ? 'warn' : 'bad'))); // 20min heartbeat target
				appendDotAndText(lastChip, `Last: ${formatDate(t.ts)}`);
				chips.appendChild(lastChip);

				const raChip = document.createElement('span');
				raChip.className = 'chip mono';
				raChip.textContent = `RA (J2000): ${Number.isFinite(t.raHours) ? t.raHours.toFixed(3) + 'h' : '—'}`;
				chips.appendChild(raChip);

				const decChip = document.createElement('span');
				decChip.className = 'chip mono';
				decChip.textContent = `Dec (J2000): ${Number.isFinite(t.decDeg) ? t.decDeg.toFixed(3) + '°' : '—'}`;
				chips.appendChild(decChip);

				if (t.tracking !== undefined && t.tracking !== null) {
					const trChip = document.createElement('span');
					trChip.className = 'chip ' + (t.tracking ? 'ok' : 'warn');
					appendDotAndText(trChip, t.tracking ? 'Tracking' : 'Not tracking');
					chips.appendChild(trChip);
				}
				if (t.slewing !== undefined && t.slewing !== null) {
					const slChip = document.createElement('span');
					slChip.className = 'chip ' + (t.slewing ? 'warn' : 'mono');
					appendDotAndText(slChip, t.slewing ? 'Slewing' : 'Idle');
					chips.appendChild(slChip);
				}

				row.appendChild(idSpan);
				row.appendChild(chips);
				li.appendChild(row);
				telList.appendChild(li);
			});
		}
	}

	async function fetchStatus() {
		try {
			const res = await fetch(`${basePath}/status`, { cache: 'no-store' });
			if (!res.ok) throw new Error(res.statusText);
			const data = await res.json();
			render(data);
		} catch (e) {
			console.error('status fetch failed', e);
		}
	}

	// initial + poll
	fetchStatus();
	setInterval(fetchStatus, 15000);
})();


