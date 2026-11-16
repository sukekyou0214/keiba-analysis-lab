// 本番サイトの「週末の全芝レース一括分析」を画面操作で動かす（運用補助。取得は本番アプリの共通ゲートを通る）。
// 使い方: node scripts/ops/weekend_prod.js start|status|results|download <保存先フォルダ>
// Playwright が必要（require('playwright')）。
const { chromium } = require('playwright');
const action = process.argv[2] || 'status';
const URL = 'https://keiba-analysis-lab2026.streamlit.app/';
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1100, height: 1400 }, acceptDownloads: true });
  const p = await ctx.newPage();
  p.setDefaultTimeout(60000);
  await p.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForTimeout(8000);
  const wake = p.locator('button', { hasText: 'back up' });
  if (await wake.count()) { await wake.first().click(); console.log('woke'); await p.waitForTimeout(120000); await p.reload(); await p.waitForTimeout(20000); }
  const F = () => p.frames().find(x => x.url().includes('/~/+/')) || p.mainFrame();
  for (let i = 0; i < 30; i++) { const t = await F().locator('body').innerText().catch(() => ''); if (/競馬分析ラボ/.test(t)) break; await p.waitForTimeout(10000); }
  await F().getByText('🧰 管理・検証').first().click(); await p.waitForTimeout(8000);
  await F().locator('[data-testid="stExpander"] summary', { hasText: '週末の全芝レース一括分析' }).first().click(); await p.waitForTimeout(3000);
  const box = F().locator('[data-testid="stExpander"]', { hasText: '週末の全芝' });
  if (action === 'start') {
    const btn = box.getByRole('button', { name: /一括分析を開始|続きから再開/ });
    const disabled = await btn.isDisabled(); console.log('start disabled', disabled);
    if (!disabled) { await btn.click(); await p.waitForTimeout(8000); }
  }
  if (action === 'results') {
    const btn = box.getByRole('button', { name: /結果を取得して照合/ });
    if (await btn.count()) { await btn.click(); console.log('results started'); await p.waitForTimeout(8000); } else console.log('results button not shown');
  }
  if (action === 'download') {
    const dir = process.argv[3];
    const btns = box.locator('[data-testid="stDownloadButton"] button');
    const n = await btns.count(); console.log('files', n);
    for (let i = 0; i < n; i++) {
      const name = (await btns.nth(i).innerText()).trim();
      const [d] = await Promise.all([p.waitForEvent('download'), btns.nth(i).click()]);
      await d.saveAs(dir + '/' + name); console.log('saved', name);
    }
  }
  await F().getByRole('button', { name: '状況を更新' }).first().click().catch(() => {}); await p.waitForTimeout(4000);
  const t = await F().locator('body').innerText(); const i = t.indexOf('週末の全芝');
  console.log((t.slice(i, i + 1500).match(/(1日目[^\n]*|\d{4}\/\d{2}\/\d{2}|状態：[^\n]*|結果：[^\n]*|結果照合：[^\n]*|対象レース：[^\n]*|取得先の制限[^\n]*)/g) || []).join('\n'));
  await b.close();
})();
