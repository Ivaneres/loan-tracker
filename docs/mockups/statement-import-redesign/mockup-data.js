/* Shared ~50-row June dataset for the statement-import redesign concepts.
   All counts, totals and summaries in the mockups are derived from this so the
   three concepts stay consistent with each other at scale. */
(function () {
  'use strict';

  function money(n) {
    return '£' + Math.abs(Number(n) || 0).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function signed(n) {
    return (Number(n) < 0 ? '−' : '+') + money(n);
  }
  function shortDay(iso) {
    var d = new Date(iso + 'T12:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  }

  var matched = [
    { d: '2024-06-02', desc: 'TESCO STORES 3124', amt: 52.30, manual: 'Weekly shop' },
    { d: '2024-06-03', desc: 'PRET A MANGER LONDON', amt: 9.80, manual: 'Lunch' },
    { d: '2024-06-04', desc: 'AMAZON MARKETPLACE', amt: 23.99, manual: 'Amazon order' },
    { d: '2024-06-05', desc: 'STARBUCKS 2291', amt: 4.20, manual: 'Coffee' },
    { d: '2024-06-06', desc: 'SAINSBURYS S/MKT', amt: 38.75, manual: 'Groceries' },
    { d: '2024-06-07', desc: 'UBER TRIP', amt: 14.50, manual: 'Uber' },
    { d: '2024-06-08', desc: 'TESCO STORES 3124', amt: 31.10, manual: 'Groceries' },
    { d: '2024-06-09', desc: 'PRET A MANGER LONDON', amt: 7.40, manual: 'Lunch' },
    { d: '2024-06-10', desc: 'AMAZON MARKETPLACE', amt: 12.49, manual: 'Household' },
    { d: '2024-06-13', desc: 'SAINSBURYS S/MKT', amt: 44.20, manual: 'Groceries' },
    { d: '2024-06-13', desc: 'UBER TRIP', amt: 11.30, manual: 'Uber' },
    { d: '2024-06-14', desc: 'PRET A MANGER LONDON', amt: 8.20, manual: 'Lunch' },
    { d: '2024-06-15', desc: 'AMAZON MARKETPLACE', amt: 56.00, manual: 'Amazon order' },
    { d: '2024-06-16', desc: 'TESCO STORES 3124', amt: 27.65, manual: 'Groceries' },
    { d: '2024-06-18', desc: 'AMAZON MARKETPLACE', amt: 17.42, manual: 'Amazon order' },
    { d: '2024-06-18', desc: 'STARBUCKS 2291', amt: 4.60, manual: 'Coffee' },
    { d: '2024-06-19', desc: 'SAINSBURYS S/MKT', amt: 39.90, manual: 'Groceries' },
    { d: '2024-06-20', desc: 'PRET A MANGER LONDON', amt: 6.80, manual: 'Lunch' },
    { d: '2024-06-22', desc: 'AMAZON MARKETPLACE', amt: 29.99, manual: 'Amazon order' },
    { d: '2024-06-22', desc: 'TFL TRAVEL CH', amt: 8.40, manual: 'Travel card' },
    { d: '2024-06-23', desc: 'STARBUCKS 2291', amt: 3.60, manual: 'Coffee' },
    { d: '2024-06-23', desc: 'SAINSBURYS S/MKT', amt: 41.15, manual: 'Groceries' },
    { d: '2024-06-24', desc: 'UBER TRIP', amt: 13.75, manual: 'Uber' },
    { d: '2024-06-25', desc: 'PRET A MANGER LONDON', amt: 9.10, manual: 'Lunch' },
    { d: '2024-06-27', desc: 'TESCO STORES 3124', amt: 22.40, manual: 'Groceries' },
    { d: '2024-06-29', desc: 'SAINSBURYS S/MKT', amt: 36.80, manual: 'Groceries' }
  ];

  var bills = [
    { d: '2024-06-01', desc: 'DIRECT LINE RENT', amt: 1150.00, plan: 'Rent' },
    { d: '2024-06-05', desc: 'SPOTIFY POABGD', amt: 10.99, plan: 'Spotify' },
    { d: '2024-06-07', desc: 'NETFLIX.COM', amt: 15.99, plan: 'Netflix' },
    { d: '2024-06-10', desc: 'PURE GYM LTD', amt: 29.00, plan: 'Gym' },
    { d: '2024-06-12', desc: 'O2 MOBILE', amt: 18.00, plan: 'Phone' },
    { d: '2024-06-17', desc: 'OCTOPUS ENERGY', amt: 68.00, plan: 'Energy' },
    { d: '2024-06-20', desc: 'VIRGIN MEDIA', amt: 32.00, plan: 'Broadband' },
    { d: '2024-06-24', desc: 'COUNCIL TAX', amt: 145.00, plan: 'Council Tax' }
  ];

  var transferPairs = [
    { out: { d: '2024-06-14', desc: 'TRANSFER TO SAVINGS', amt: 100.00 },
      in:  { d: '2024-06-16', desc: 'FROM SAVINGS', amt: 100.00 } },
    { out: { d: '2024-06-28', desc: 'TRANSFER TO ISA', amt: 250.00 },
      in:  { d: '2024-06-28', desc: 'FROM CURRENT AC', amt: 250.00 } }
  ];

  var incoming = [
    { d: '2024-06-25', desc: 'SALARY ACME LTD', amt: 2300.00 },
    { d: '2024-06-28', desc: 'REFUND AMAZON', amt: 45.00 },
    { d: '2024-06-30', desc: 'INTEREST PAID', amt: 3.12 }
  ];

  var fresh = [
    { d: '2024-06-03', desc: 'TFL TRAVEL CH', amt: 2.80, src: 'Amex' },
    { d: '2024-06-09', desc: 'TFL TRAVEL CH', amt: 5.60, src: 'Amex' },
    { d: '2024-06-17', desc: 'TFL TRAVEL CH', amt: 2.80, src: 'Amex' },
    { d: '2024-06-27', desc: 'AMEX COFFEE CO', amt: 4.20, src: 'Amex' }
  ];

  var asks = [
    { type: 'near', d: '2024-06-08', desc: 'UBER TRIP 4X9K', amt: 8.75,
      candidates: [{ desc: 'Uber ride', amt: 8.50, d: '2024-06-08' }] },
    { type: 'collision', d: '2024-06-11', desc: 'TESCO STORES 3124', amt: 12.00,
      candidates: [{ desc: 'Tesco', amt: 12.00, d: '2024-06-11' }, { desc: 'Groceries', amt: 12.00, d: '2024-06-12' }] },
    { type: 'near', d: '2024-06-21', desc: 'TESCO STORES 3124', amt: 18.30,
      candidates: [{ desc: 'Groceries', amt: 18.00, d: '2024-06-20' }] },
    { type: 'collision', d: '2024-06-24', desc: 'TESCO STORES 3124', amt: 15.00,
      candidates: [{ desc: 'Tesco', amt: 15.00, d: '2024-06-24' }, { desc: 'Household', amt: 15.00, d: '2024-06-23' }] },
    { type: 'near', d: '2024-06-28', desc: 'UBER TRIP 8K2', amt: 16.00,
      candidates: [{ desc: 'Uber', amt: 15.60, d: '2024-06-28' }] }
  ];

  var leftoverManuals = [
    { d: '2024-06-15', desc: 'Farmers market', amt: 12.50 },
    { d: '2024-06-20', desc: 'Cash withdrawal', amt: 20.00 }
  ];

  var missingBill = { plan: 'Water', amt: 24.00 };

  // Daily → Plan expected bills for the month.
  var expectedBills = [
    { plan: 'Rent', amt: 1150.00 },
    { plan: 'Council Tax', amt: 145.00 },
    { plan: 'Energy', amt: 68.00 },
    { plan: 'Broadband', amt: 32.00 },
    { plan: 'Gym', amt: 29.00 },
    { plan: 'Phone', amt: 18.00 },
    { plan: 'Netflix', amt: 15.99 },
    { plan: 'Spotify', amt: 10.99 },
    { plan: 'Water', amt: 24.00 }
  ];

  function srcFor(i) { return i % 5 === 3 ? 'Amex' : 'Monzo'; }

  /* Flat, date-sorted list of every statement row with its verdict. */
  function allRows() {
    var out = [];
    var i = 0;
    matched.forEach(function (r) {
      out.push({ d: r.d, desc: r.desc, amt: r.amt, dir: 'out', src: r.src || srcFor(i++),
        kind: 'matched', verdict: 'Matched', becomes: '“' + r.manual + '”', amountLabel: money(r.amt) });
    });
    bills.forEach(function (r) {
      out.push({ d: r.d, desc: r.desc, amt: r.amt, dir: 'out', src: r.src || srcFor(i++),
        kind: 'bill', verdict: 'Bill', becomes: 'Plan “' + r.plan + '” · not daily spend', amountLabel: money(r.amt) });
    });
    transferPairs.forEach(function (p) {
      out.push({ d: p.out.d, desc: p.out.desc, amt: p.out.amt, dir: 'out', src: srcFor(i++),
        kind: 'transfer', verdict: 'Transfer', becomes: 'paired with +' + money(p.in.amt) + ' on ' + shortDay(p.in.d), amountLabel: money(p.out.amt) });
      out.push({ d: p.in.d, desc: p.in.desc, amt: p.in.amt, dir: 'in', src: srcFor(i++),
        kind: 'transfer', verdict: 'Transfer', becomes: 'paired with −' + money(p.out.amt) + ' on ' + shortDay(p.out.d), amountLabel: '+' + money(p.in.amt) });
    });
    incoming.forEach(function (r) {
      out.push({ d: r.d, desc: r.desc, amt: r.amt, dir: 'in', src: r.src || srcFor(i++),
        kind: 'incoming', verdict: 'Incoming', becomes: 'money in', amountLabel: '+' + money(r.amt) });
    });
    fresh.forEach(function (r) {
      out.push({ d: r.d, desc: r.desc, amt: r.amt, dir: 'out', src: r.src || srcFor(i++),
        kind: 'new', verdict: 'New', becomes: 'no match — new spending', amountLabel: money(r.amt) });
    });
    asks.forEach(function (a) {
      out.push({ d: a.d, desc: a.desc, amt: a.amt, dir: 'out', src: srcFor(i++),
        kind: 'ask', verdict: a.type === 'near' ? 'Near match' : (a.candidates.length + ' exact matches'),
        becomes: a.type === 'near' ? '≈ “' + a.candidates[0].desc + '” ' + money(a.candidates[0].amt)
                                  : 'pick one of ' + a.candidates.length,
        amountLabel: money(a.amt), ask: a });
    });
    out.sort(function (a, b) { return a.d < b.d ? -1 : a.d > b.d ? 1 : 0; });
    return out;
  }

  function totals() {
    var out = allRows();
    var sum = function (arr) { return arr.reduce(function (s, r) { return s + r.amt; }, 0); };
    var statementCount = out.length;
    var outgoing = out.filter(function (r) { return r.dir === 'out'; });
    var incomingRows = out.filter(function (r) { return r.dir === 'in'; });
    var matchedTotal = sum(matched);
    var billTotal = sum(bills);
    var transferOutTotal = transferPairs.reduce(function (s, p) { return s + p.out.amt; }, 0);
    var transferInTotal = transferPairs.reduce(function (s, p) { return s + p.in.amt; }, 0);
    var incomingTotal = sum(incoming);
    var newTotal = sum(fresh);
    var expectedBillTotal = expectedBills.reduce(function (s, b) { return s + b.amt; }, 0);
    var defaultMatchTotal = asks.reduce(function (s, a) { return s + a.candidates[0].amt; }, 0);
    return {
      statementCount: statementCount,
      bankTotal: sum(outgoing),
      matchedCount: matched.length,
      matchedTotal: matchedTotal,
      matchedPlusDefaults: matchedTotal + defaultMatchTotal,
      billCount: bills.length,
      billTotal: billTotal,
      expectedBillTotal: expectedBillTotal,
      expectedBillCount: expectedBills.length,
      transferCount: transferPairs.length,
      transferOutTotal: transferOutTotal,
      transferInTotal: transferInTotal,
      incomingCount: incoming.length,
      incomingTotal: incomingTotal,
      newCount: fresh.length,
      newTotal: newTotal,
      askCount: asks.length,
      outgoingCount: outgoing.length,
      sortedCount: statementCount - asks.length
    };
  }

  window.MOCK = {
    month: 'June 2024',
    matched: matched,
    bills: bills,
    transferPairs: transferPairs,
    incoming: incoming,
    fresh: fresh,
    asks: asks,
    leftoverManuals: leftoverManuals,
    missingBill: missingBill,
    expectedBills: expectedBills,
    allRows: allRows,
    totals: totals,
    money: money,
    signed: signed,
    shortDay: shortDay
  };
})();
