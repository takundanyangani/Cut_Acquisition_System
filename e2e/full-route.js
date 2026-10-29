// Browser test: one equipment request through every CUT ICT 38 step, then
// delivery, issue voucher and the user's signature, with an axe-core
// accessibility scan of each page on the way.
//
// Needs a server with the demo data (python manage.py seed_demo):
//   cd e2e && npm install && BASE=http://127.0.0.1:8000 npm test
// Set SHOTS=/some/folder to save screenshots.
const { chromium } = require('playwright');
const fs = require('fs');
const os = require('os');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:8000';
const PASS = process.env.DEMO_PASSWORD || 'Demo-Pass-2026';
const PDF = path.join(os.tmpdir(), 'cutacq-e2e-spec-sheet.pdf');
fs.writeFileSync(PDF, '%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');
const AXE = require.resolve('axe-core/axe.min.js');
const SHOTS = process.env.SHOTS;
const RUN = Date.now().toString(36).toUpperCase();

const problems = [];
const axeFindings = [];
let browser;

async function as(email, fn, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1440, height: 900 }, colorScheme: opts.scheme || 'light' });
  const page = await ctx.newPage();
  page.on('console', m => {
    // The deliberate 404 check logs one expected resource error.
    if (m.type() === 'error' && !page.url().includes('/missing-page/')) problems.push(`${email}: ${m.text()} @ ${page.url()}`);
  });
  page.on('pageerror', e => problems.push(`${email}: pageerror ${e.message}`));
  await page.goto(BASE + '/accounts/sign-in/');
  await page.fill('#id_username', email);
  await page.fill('#id_password', PASS);
  await Promise.all([page.waitForNavigation(), page.click('form button[type=submit]')]);
  try { await fn(page); } finally { await ctx.close(); }
}

async function axe(page, label) {
  const ctx2 = await browser.newContext({ bypassCSP: true, storageState: await page.context().storageState(), viewport: { width: 1440, height: 900 } });
  const p2 = await ctx2.newPage();
  await p2.goto(page.url());
  await p2.addScriptTag({ path: AXE });
  const result = await p2.evaluate(async () => await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice'] }));
  for (const v of result.violations) {
    axeFindings.push(`${label}: [${v.impact}] ${v.id} - ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target.join(' ')}`);
  }
  await ctx2.close();
}

async function act(page, detailUrl, linkText, fill) {
  await page.goto(detailUrl);
  await page.click(`.action-panel a:has-text("${linkText}")`);
  await page.waitForLoadState();
  if (fill) await fill(page);
  await Promise.all([page.waitForNavigation(), page.click('form.stack button[type=submit]')]);
  const ok = await page.locator('.alert-success').count();
  if (!ok) {
    const err = await page.locator('.error-summary').innerText().catch(() => '');
    throw new Error(`${linkText} failed: ${err || page.url()}`);
  }
}

async function shot(page, name, full = true) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.jpg`), fullPage: full, type: 'jpeg', quality: 82 });
}

(async () => {
  browser = await chromium.launch();
  let detail;
  // 1. Requester creates a two-item equipment request with an attachment.
  await as('tendai.moyo@demo.cut.local', async page => {
    await page.goto(BASE + '/requests/new/equipment/');
    await page.fill('#id_title', 'Laptops for the robotics club');
    await page.fill('#id_justification', 'The robotics club meets three times a week and has no machines for programming the controllers.');
    await page.selectOption('#id_items-0-category', { label: 'Laptop' });
    await page.fill('#id_items-0-description', 'Laptop with 16 GB memory');
    await page.fill('#id_items-0-quantity', '2');
    await page.click('[data-formset-add]');
    await page.selectOption('#id_items-1-category', { label: 'Peripheral (mouse, keyboard, headset)' });
    await page.fill('#id_items-1-description', 'USB mouse');
    await page.click('[data-formset-add]');
    await page.locator('[data-formset-row]').nth(2).locator('[data-formset-remove]').click();
    if (await page.locator('[data-formset-row]').count() !== 2) throw new Error('formset remove failed');
    await page.setInputFiles('#id_attachments', PDF);
    await shot(page, 'new-request-form');
    await axe(page, 'new request form');
    await Promise.all([page.waitForNavigation(), page.click('button[name=submit]')]);
    detail = page.url();
    const stamp = await page.locator('.stamp-lg').innerText();
    if (!stamp.includes('Personal Advisor')) throw new Error('not with PA: ' + stamp);
    const items = await page.locator('#s2').locator('xpath=ancestor::section').locator('tbody tr').count();
    if (items !== 2) throw new Error('expected 2 items, got ' + items);
  });
  console.log('created', detail);

  await as('farai.ncube@demo.cut.local', async page => {
    await page.goto(BASE + '/requests/queue/');
    await axe(page, 'queue');
    await act(page, detail, 'Forward to the ICT Director', p => p.fill('#id_comment', 'Complete.'));
  });
  await as('rumbidzai.mutasa@demo.cut.local', async page => {
    await act(page, detail, 'Comment and forward to the CSM', async p => {
      await p.fill('#id_comment', 'Supported for the club. Check stock first.');
      await p.fill('#id_password', 'wrong-password');
    }).then(() => { throw new Error('wrong password accepted'); }, async e => {
      if (!(await page.locator('#id_password_error').count())) throw e;
    });
    await page.fill('#id_comment', 'Supported for the club. Check stock first.');
    await page.fill('#id_password', PASS);
    await shot(page, 'sign-decision', false);
    await axe(page, 'signing page');
    await Promise.all([page.waitForNavigation(), page.click('form.stack button[type=submit]')]);
    if (!(await page.locator('.alert-success').count())) throw new Error('director forward failed');
  });
  await as('tatenda.sibanda@demo.cut.local', async page => {
    await act(page, detail, 'Assign a technician', p => p.selectOption('#id_technician', { label: 'Kudakwashe Dube' }));
  });
  await as('kuda.dube@demo.cut.local', async page => {
    await act(page, detail, 'Submit specifications', async p => {
      for (const t of await p.locator('textarea[name^=spec_]').all()) await t.fill('Core i5, 16 GB RAM, 512 GB SSD, 14-inch');
      for (const c of await p.locator('input[name^=cost_]').all()) await c.fill('950');
      await p.setInputFiles('#id_spec_files', PDF);
      await axe(p, 'specifications page');
    });
  });
  await as('chiedza.marufu@demo.cut.local', async page => {
    await act(page, detail, 'Confirm specifications', p => p.fill('#id_password', PASS));
  });
  await as('rumbidzai.mutasa@demo.cut.local', async page => {
    await act(page, detail, 'Approve purchase request', p => p.fill('#id_password', PASS));
  });
  await as('kuda.dube@demo.cut.local', async page => {
    await act(page, detail, 'Send to Procurement', p => p.fill('#id_procurement_reference', 'PR-2026-2201'));
    await act(page, detail, 'Record delivery', async p => {
      await p.selectOption('#id_asset-0-category', { label: 'Laptop' });
      await p.fill('#id_asset-0-make', 'Lenovo');
      await p.fill('#id_asset-0-model', 'ThinkPad E14');
      await p.fill('#id_asset-0-serial_number', `PF-${RUN}-1`);
      await p.fill('#id_asset-0-specification', 'Core i5, 16 GB, 512 GB SSD');
      await p.click('[data-formset-add]');
      await p.selectOption('#id_asset-1-category', { label: 'Laptop' });
      await p.fill('#id_asset-1-make', 'Lenovo');
      await p.fill('#id_asset-1-model', 'ThinkPad E14');
      await p.fill('#id_asset-1-serial_number', `PF-${RUN}-2`);
      await p.fill('#id_asset-1-specification', 'Core i5, 16 GB, 512 GB SSD');
      await p.fill('#id_supplier', 'Demo Supplies (Pvt) Ltd');
      await p.check('#id_delivery_complete');
      await shot(p, 'record-delivery');
      await axe(p, 'delivery page');
    });
    await act(page, detail, 'Raise issue voucher', async p => {
      const boxes = p.locator('input[name=assets]:checked');
      if (await boxes.count() !== 2) throw new Error('delivered assets should be preselected');
      await p.fill('#id_comment', 'The mouse comes from the Helpdesk drawer and is not tracked as an asset.');
      await shot(p, 'raise-voucher');
      await axe(p, 'voucher page');
    });
  });
  await as('tendai.moyo@demo.cut.local', async page => {
    await act(page, detail, 'Sign for receipt', async p => {
      await p.check('#id_confirm_serials');
      await p.check('#id_confirm_specification');
      await p.check('#id_confirm_condition');
      await p.fill('#id_password', PASS);
      await shot(p, 'sign-for-receipt');
      await axe(p, 'acknowledge page');
    });
    const stamp = await page.locator('.stamp-lg').innerText();
    if (!stamp.includes('Completed')) throw new Error('not completed: ' + stamp);
    await shot(page, 'request-completed');
    await axe(page, 'completed request detail');
    await page.goto(detail + 'print/');
    if (!(await page.locator('text=Audit chain intact').count())) throw new Error('print record chain check missing');
    await shot(page, 'print-record');
    await page.goto(BASE + '/accounts/profile/');
    if (!(await page.locator(`text=PF-${RUN}-1`).count())) throw new Error('asset not on profile');
    await axe(page, 'profile');
  });
  // Mobile navigation drawer and theme toggle.
  await as('tendai.moyo@demo.cut.local', async page => {
    await page.click('[data-nav-toggle]');
    await page.waitForTimeout(300);
    if (!(await page.locator('.shell.nav-open').count())) throw new Error('drawer did not open');
    await shot(page, 'mobile-nav', false);
    await page.keyboard.press('Escape');
    if (await page.locator('.shell.nav-open').count()) throw new Error('drawer did not close');
    await page.click('[data-theme-toggle]');
    const theme = await page.evaluate(() => document.documentElement.dataset.theme);
    if (theme !== 'dark') throw new Error('theme toggle failed: ' + theme);
  }, { viewport: { width: 375, height: 812 } });
  // Accessibility of the remaining main pages.
  await as('rumbidzai.mutasa@demo.cut.local', async page => {
    for (const [label, url] of [['dashboard', '/'], ['reports', '/reports/'], ['process', '/process/'],
                                ['all requests', '/requests/all/'], ['choose kind', '/requests/new/'],
                                ['delegations', '/accounts/delegations/'], ['delegation form', '/accounts/delegations/new/'],
                                ['notifications', '/notifications/'], ['404', '/missing-page/']]) {
      await page.goto(BASE + url);
      await axe(page, label);
    }
    await page.goto(detail);
    await axe(page, 'request detail (director)');
  });
  await as('kuda.dube@demo.cut.local', async page => {
    for (const [label, url] of [['assets', '/assets/'], ['vouchers', '/assets/vouchers/'], ['asset form', '/assets/new/']]) {
      await page.goto(BASE + url);
      await axe(page, label);
    }
  });
  {
    const ctx = await browser.newContext({ bypassCSP: true });
    const p = await ctx.newPage();
    await p.goto(BASE + '/accounts/sign-in/');
    await p.addScriptTag({ path: AXE });
    const r = await p.evaluate(async () => await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice'] }));
    for (const v of r.violations) axeFindings.push(`sign-in: [${v.impact}] ${v.id} - ${v.help} e.g. ${v.nodes[0].target.join(' ')}`);
    await ctx.close();
  }
  await browser.close();
  console.log(problems.length ? 'console problems:\n' + problems.join('\n') : 'no console errors');
  console.log(axeFindings.length ? 'axe findings:\n' + axeFindings.join('\n') : 'no axe violations');
  if (problems.length || axeFindings.length) process.exit(1);
  console.log('E2E PASSED');
})().catch(async e => { console.error('E2E FAILED:', e.message); if (browser) await browser.close(); process.exit(1); });
