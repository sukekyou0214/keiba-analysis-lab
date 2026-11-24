// 本番サイトの「地方の1レース取得（研究用）」を操作する運用補助（取得は共通ゲートを通る）。
// 使い方: NAR_DATE=YYYYMMDD NAR_KEY=レース名 node scripts/ops/nar_prod.js start|status|zip <保存先>
const { chromium } = require('playwright');
const action = process.argv[2] || 'status';
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1100, height: 1400 }, acceptDownloads: true });
  const p = await ctx.newPage(); p.setDefaultTimeout(60000);
  await p.goto('https://keiba-analysis-lab2026.streamlit.app/', { waitUntil: 'domcontentloaded', timeout: 90000 }); await p.waitForTimeout(10000);
  const wake = p.locator('button', { hasText: 'back up' });
  if (await wake.count()) { await wake.first().click(); console.log('woke'); await p.waitForTimeout(120000); await p.reload(); await p.waitForTimeout(20000); }
  const F = () => p.frames().find(x => x.url().includes('/~/+/')) || p.mainFrame();
  for (let i = 0; i < 30; i++) { const t = await F().locator('body').innerText().catch(() => ''); if (/競馬分析ラボ/.test(t)) break; await p.waitForTimeout(10000); }
  await F().getByText('🧰 管理・検証').first().click(); await p.waitForTimeout(8000);
  await F().locator('[data-testid="stExpander"] summary', { hasText: '地方の1レース取得' }).first().click(); await p.waitForTimeout(3000);
  const box = F().locator('[data-testid="stExpander"]', { hasText: '地方の1レース取得' });
  if (process.env.NAR_DATE) { const d = box.getByLabel('開催日（YYYYMMDD）'); await d.fill(process.env.NAR_DATE); await d.press('Tab'); await p.waitForTimeout(3000); }
  if (process.env.NAR_KEY) { const k = box.getByLabel(/レース名（一部）/); await k.fill(process.env.NAR_KEY); await k.press('Tab'); await p.waitForTimeout(3000); }
  if (action === 'start') { const btn = box.getByRole('button', { name: '地方の1レースを取得' }); console.log('disabled', await btn.isDisabled()); if (!(await btn.isDisabled())) { await btn.click(); await p.waitForTimeout(8000); } }
  if (action === 'zip') { const dir = process.argv[3]; await box.getByRole('button', { name: 'ZIPを作成' }).click(); await p.waitForTimeout(15000);
    const dl = box.locator('[data-testid="stDownloadButton"] button', { hasText: '.zip' }); const name = (await dl.first().innerText()).trim();
    const [d] = await Promise.all([p.waitForEvent('download'), dl.first().click()]); await d.saveAs(dir + '/' + name); console.log('saved', name); }
  await box.getByRole('button', { name: '状況を更新' }).click().catch(() => {}); await p.waitForTimeout(3000);
  console.log((await box.innerText()).split('\n').filter(l => /状態：|レースID|一覧表記|対象馬|結果/.test(l)).join('\n'));
  await b.close();
})();
