"""Shared request gate for netkeiba hosts.

Every request to a netkeiba host goes through one gate per process:
- a minimum interval per host (default 3 seconds, adjustable),
- an optional per-operation request budget (e.g. 40 requests per button press),
- after consecutive refusals (HTTP 400/403/429/503 or transport errors) the host is
  closed until a stored "next allowed" time (30 min, doubled on repeat, max 6 h).

The gate never retries, never rotates access paths and never runs requests in parallel.
It only waits or stops.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

REFUSAL_STATUS = frozenset({400, 403, 429, 503})
DEFAULT_MIN_INTERVAL = 3.0
MIN_INTERVAL_BOUNDS = (2.0, 30.0)
DEFAULT_BUDGET = 40
BASE_COOLDOWN = 30 * 60
MAX_COOLDOWN = 6 * 60 * 60
JST = timezone(timedelta(hours=9))
STATE_PATH = Path(os.environ.get('KEIBA_FETCH_STATE', '/tmp/keiba_analysis_v141/fetch_policy.json'))


class PolicyStop(requests.RequestException):
    """The gate stopped a request before it was sent. Callers should stop their loop."""


class CooldownActive(PolicyStop):
    def __init__(self, host, until):
        self.host = host
        self.until = until
        super().__init__(f'{host} は取得先の制限のため {format_time(until)} まで取得を停止中です')


class BudgetExhausted(PolicyStop):
    def __init__(self, limit):
        self.limit = limit
        super().__init__(f'今回の取得上限（{limit}件）に達しました。残りは次回の操作で取得します')


class RefusedResponse(requests.HTTPError):
    """The host answered with a status treated as a refusal."""


def format_time(epoch):
    return datetime.fromtimestamp(float(epoch), JST).strftime('%m/%d %H:%M')


def is_gated(url):
    host = (urlparse(url).hostname or '').lower()
    return host == 'netkeiba.com' or host.endswith('.netkeiba.com')


def _empty_host():
    return {'requests': 0, 'success': 0, 'refused': 0, 'consecutive': 0, 'level': 0,
            'cooldown_until': 0.0, 'last_request': 0.0, 'last_reason': ''}


class _Budget:
    def __init__(self, limit):
        self.limit = int(limit)
        self.used = 0


class FetchPolicy:
    def __init__(self, state_path=None, clock=time.time, sleeper=time.sleep, transport=None,
                 max_refusals=2, base_cooldown=BASE_COOLDOWN, max_cooldown=MAX_COOLDOWN):
        self.state_path = Path(state_path) if state_path else None
        self.clock = clock
        self.sleeper = sleeper
        self.transport = transport
        self.max_refusals = max_refusals
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self._lock = threading.RLock()
        self._local = threading.local()
        self._state = self._load()

    # ---- state -------------------------------------------------------------
    def _load(self):
        state = {'min_interval': DEFAULT_MIN_INTERVAL, 'hosts': {}}
        if self.state_path and self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text(encoding='utf-8'))
                if isinstance(saved, dict):
                    state['min_interval'] = float(saved.get('min_interval', DEFAULT_MIN_INTERVAL))
                    for host, values in (saved.get('hosts') or {}).items():
                        state['hosts'][host] = {**_empty_host(), **values}
            except (OSError, ValueError, TypeError):
                pass
        state['min_interval'] = min(max(state['min_interval'], MIN_INTERVAL_BOUNDS[0]), MIN_INTERVAL_BOUNDS[1])
        return state

    def _save(self):
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.state_path)
        except OSError:
            pass

    def _host(self, host):
        return self._state['hosts'].setdefault(host, _empty_host())

    @property
    def min_interval(self):
        return self._state['min_interval']

    def set_min_interval(self, seconds):
        low, high = MIN_INTERVAL_BOUNDS
        with self._lock:
            self._state['min_interval'] = min(max(float(seconds), low), high)
            self._save()

    # ---- budget ------------------------------------------------------------
    @contextlib.contextmanager
    def budget(self, limit=DEFAULT_BUDGET):
        previous = getattr(self._local, 'budget', None)
        current = _Budget(limit)
        self._local.budget = current
        try:
            yield current
        finally:
            self._local.budget = previous

    # ---- queries -----------------------------------------------------------
    def cooldown_until(self, host=None):
        """Latest active cooldown end (epoch) for one host or any netkeiba host, else None."""
        now = self.clock()
        known = dict(self._state['hosts'])  # read without waiting for an in-flight request
        hosts = [host] if host else list(known)
        ends = [known[h]['cooldown_until'] for h in hosts if h in known]
        ends = [e for e in ends if e > now]
        return max(ends) if ends else None

    def snapshot(self):
        now = self.clock()
        rows = []
        if True:
            for host, h in sorted(dict(self._state['hosts']).items()):
                active = h['cooldown_until'] > now
                rows.append({'取得先': host, '要求数': h['requests'], '成功': h['success'], '拒否・通信エラー': h['refused'],
                             '状態': '停止中' if active else '取得可',
                             '次に取得できる時刻': format_time(h['cooldown_until']) if active else '—',
                             '直近の拒否理由': h['last_reason'] or '—'})
        return rows

    # ---- request -----------------------------------------------------------
    def get(self, url, transport=None, **kwargs):
        send = transport or self.transport or requests.get
        if not is_gated(url):
            return send(url, **kwargs)
        host = urlparse(url).hostname.lower()
        with self._lock:
            h = self._host(host)
            now = self.clock()
            if h['cooldown_until'] > now:
                raise CooldownActive(host, h['cooldown_until'])
            budget = getattr(self._local, 'budget', None)
            if budget is not None and budget.used >= budget.limit:
                raise BudgetExhausted(budget.limit)
            wait = h['last_request'] + self.min_interval - now
            if wait > 0:
                self.sleeper(wait)
            if budget is not None:
                budget.used += 1
            h['requests'] += 1
            h['last_request'] = self.clock()
            self._save()
            try:
                response = send(url, **kwargs)
            except requests.RequestException as exc:
                self._refused(host, type(exc).__name__)
                raise
            status = getattr(response, 'status_code', None)
            if isinstance(status, int) and status in REFUSAL_STATUS:
                self._refused(host, f'HTTP {response.status_code}')
                raise RefusedResponse(f'{response.status_code} 取得先が応答を拒否しました: {url}', response=response)
            h['success'] += 1
            h['consecutive'] = 0
            h['level'] = 0
            h['last_request'] = self.clock()
            self._save()
            return response

    def _refused(self, host, reason):
        h = self._host(host)
        h['refused'] += 1
        h['consecutive'] += 1
        h['last_reason'] = reason
        h['last_request'] = self.clock()
        if h['consecutive'] >= self.max_refusals:
            seconds = min(self.base_cooldown * (2 ** h['level']), self.max_cooldown)
            h['cooldown_until'] = self.clock() + seconds
            h['level'] += 1
            h['consecutive'] = 0
            self._save()
            raise CooldownActive(host, h['cooldown_until'])
        self._save()


POLICY = FetchPolicy(state_path=STATE_PATH)


def policy_get(url, **kwargs):
    return POLICY.get(url, **kwargs)


def page_encoding(response, default='utf-8'):
    """ページ自身の宣言（<meta charset> → Content-Type）で文字コードを決める。決め打ちしない。

    netkeibaの馬成績ページは EUC-JP から UTF-8 に変わったことがある（2026-11）。
    """
    import re
    head = response.content[:4096]
    hit = re.search(rb'<meta[^>]+charset=["\']?([A-Za-z0-9_\-]+)', head, re.I)
    if hit:
        return hit.group(1).decode('ascii').lower()
    content_type = response.headers.get('Content-Type', '') if hasattr(response, 'headers') else ''
    hit = re.search(r'charset=([A-Za-z0-9_\-]+)', content_type, re.I)
    if hit:
        return hit.group(1).lower()
    return getattr(response, 'apparent_encoding', None) or default
