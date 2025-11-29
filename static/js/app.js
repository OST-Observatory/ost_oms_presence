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
				li.textContent = `${h.hostId || 'host'} — last: ${formatDate(h.ts)} • OS: ${h.osVersion || '—'} • CPU: ${Math.round(h.cpuPercent||0)}% • RAM: ${Math.round(h.memPercent||0)}% • C free: ${Math.round(h.diskCPercent||0)}% • Uptime: ${fmtIntSecToHhMm(h.uptimeSec)}`;
				list.appendChild(li);
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


