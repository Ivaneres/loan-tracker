/**
 * Bill-list diff helpers for Daily Budget "Pull from statements".
 * Browser: attaches to window.DbBillDiff
 * Node: module.exports for unit tests
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.DbBillDiff = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function normalizeBillLabel(s) {
    return String(s || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function billAmount(b) {
    const n = Number(b && b.amount);
    return Number.isFinite(n) ? Math.round(n * 100) / 100 : 0;
  }

  function amountsClose(a, b) {
    return Math.abs(billAmount({ amount: a }) - billAmount({ amount: b })) < 0.005;
  }

  function stripBillDiffMeta(b) {
    const out = Object.assign({}, b);
    delete out.changeKind;
    delete out.previousAmount;
    delete out._norm;
    return out;
  }

  /**
   * Diff previous curated bills against a fresh statement estimate.
   * Match by normalized label (first unmatched previous wins).
   */
  function diffBillPull(previous, next) {
    const prev = Array.isArray(previous) ? previous : [];
    const nxt = Array.isArray(next) ? next : [];
    const prevBuckets = new Map();
    prev.forEach(function (b, idx) {
      const key = normalizeBillLabel(b.label);
      if (!key) return;
      if (!prevBuckets.has(key)) prevBuckets.set(key, []);
      prevBuckets.get(key).push({ b: b, idx: idx });
    });
    const usedPrev = new Set();
    const items = nxt.map(function (raw) {
      const b = Object.assign({}, raw);
      const key = normalizeBillLabel(b.label);
      const bucket = key ? prevBuckets.get(key) : null;
      let match = null;
      if (bucket && bucket.length) {
        match = bucket.shift();
        usedPrev.add(match.idx);
      }
      if (!match) {
        b.changeKind = 'new';
        return b;
      }
      const prevAmt = billAmount(match.b);
      const nextAmt = billAmount(b);
      if (!amountsClose(prevAmt, nextAmt)) {
        b.changeKind = 'changed';
        b.previousAmount = prevAmt;
      } else {
        b.changeKind = 'same';
        b.included = match.b.included !== false;
      }
      return b;
    });
    const removed = prev
      .filter(function (_, idx) {
        return !usedPrev.has(idx);
      })
      .map(function (b) {
        const row = stripBillDiffMeta(b);
        row.changeKind = 'removed';
        return row;
      });
    const summary = {
      newCount: items.filter(function (b) {
        return b.changeKind === 'new';
      }).length,
      changedCount: items.filter(function (b) {
        return b.changeKind === 'changed';
      }).length,
      sameCount: items.filter(function (b) {
        return b.changeKind === 'same';
      }).length,
      removedCount: removed.length,
      hadBaseline: prev.length > 0,
    };
    return { items: items, removed: removed, summary: summary };
  }

  function formatBillDiffSummary(summary) {
    if (!summary || !summary.hadBaseline) return '';
    const parts = [];
    if (summary.newCount) parts.push(summary.newCount + ' new');
    if (summary.changedCount) parts.push(summary.changedCount + ' changed');
    if (summary.sameCount) parts.push(summary.sameCount + ' unchanged');
    if (summary.removedCount) parts.push(summary.removedCount + ' removed');
    if (!parts.length) return 'No bill changes vs your previous list.';
    return 'Compared to your previous list: ' + parts.join(' · ') + '.';
  }

  return {
    normalizeBillLabel: normalizeBillLabel,
    billAmount: billAmount,
    amountsClose: amountsClose,
    stripBillDiffMeta: stripBillDiffMeta,
    diffBillPull: diffBillPull,
    formatBillDiffSummary: formatBillDiffSummary,
  };
});
