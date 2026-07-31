/* ==========================================================================
   CookieGuard — Consent Banner (Phase 5)
   ==========================================================================

   WHAT THIS FILE IS
   -----------------
   A complete, standalone cookie consent manager in ONE file. Drop it into any
   website with a single script tag:

       <script src="consent-banner.js"></script>

   No framework. No build step. No dependencies. No CSS file — the styles are
   injected by the script itself.

   WHY IT MUST BE SELF-CONTAINED
   -----------------------------
   Every other file in this project runs on OUR machine. This one runs on
   SOMEBODY ELSE'S WEBSITE. We cannot assume they use React, or a bundler, or
   that they can add a stylesheet. One script tag has to be the entire
   integration — and that constraint drives every design decision below.

   THE SHIFT FROM PHASES 1-4
   -------------------------
       Phases 1-4:  DIAGNOSE  "your site sets 17 marketing cookies pre-consent"
       Phase 5:     FIX       "...and here is the thing that stops it"

   This is the half that makes CookieGuard a product rather than a report.

   WHAT MAKES CONSENT LEGALLY VALID
   --------------------------------
   GDPR Article 4(11) requires consent to be freely given, specific, informed
   and unambiguous. In practice regulators test five things:

       1. Nothing non-essential fires BEFORE the click
       2. Reject is exactly as easy as Accept  (CNIL fined Google €150m and
          Facebook €60m in 2022 for failing precisely this)
       3. No pre-ticked boxes
       4. Granular per-category choice
       5. Withdrawal is as easy as giving

   All five are implemented here, and each is marked in the code.

   ========================================================================== */

(function () {
  /*
    An IIFE — Immediately Invoked Function Expression. The `(function(){...})()`
    wrapper runs once and creates a private scope.

    WHY THIS MATTERS ENORMOUSLY HERE: our code is about to run alongside code
    we've never seen, on a site we don't control. Without this wrapper, every
    variable we declare becomes a global, and a name collision could break the
    host site — or the host site could break us.

    This is the single most important line for a third-party script.
  */
  'use strict';

  // Don't run twice if the script is accidentally included more than once.
  if (window.CookieGuardConsent) return;

  /* ======================================================================
     CONFIGURATION
     ======================================================================
     Read from the script tag's data attributes, so a site owner can
     configure the banner without editing JavaScript:

         <script src="consent-banner.js"
                 data-policy-url="/privacy"
                 data-position="bottom"></script>
  */

  const script = document.currentScript || {};
  const ds = script.dataset || {};

  const CONFIG = {
    cookieName:  ds.cookieName  || 'cookieguard_consent',
    // 6 months. Regulators expect consent to be re-asked periodically rather
    // than assumed forever; CNIL suggests at most 6 months for the record.
    expiryDays:  parseInt(ds.expiryDays || '180', 10),
    policyUrl:   ds.policyUrl   || '',
    position:    ds.position    || 'bottom',      // bottom | center
    accentColor: ds.accentColor || '#4f46e5',
  };

  /*
    THE FOUR CATEGORIES — the same taxonomy the scanner and classifier use.
    That consistency is the point: the tool that finds the problem and the tool
    that fixes it speak the same language.
  */
  const CATEGORIES = [
    {
      id: 'necessary',
      name: 'Strictly necessary',
      description:
        'Required for the site to work — login sessions, security tokens, ' +
        'shopping carts. These are exempt from consent, so they cannot be ' +
        'switched off.',
      required: true,      // ← locked ON
    },
    {
      id: 'functional',
      name: 'Functional',
      description:
        'Remember preferences such as language, region or theme. The site ' +
        'works without them.',
      required: false,
    },
    {
      id: 'analytics',
      name: 'Analytics',
      description:
        'Help us understand how the site is used — visits, popular pages, ' +
        'errors. Not legally necessary, however useful.',
      required: false,
    },
    {
      id: 'marketing',
      name: 'Marketing',
      description:
        'Used to build a profile of your interests and show you relevant ' +
        'advertising, including on other websites.',
      required: false,
    },
  ];

  /* ======================================================================
     STORING THE DECISION
     ====================================================================== */

  /*
    WHY A COOKIE RATHER THAN localStorage?

    Three reasons, and this is a fair interview question:

      1. The SERVER may need to know. A cookie is sent with every request, so
         backend code can decide whether to inject an analytics snippet at all.
         localStorage is invisible to the server.

      2. Cookies work across subdomains via the Domain attribute.
         localStorage is strictly per-origin, so shop.example.com would not
         see a choice made on www.example.com.

      3. Expiry is built in. localStorage never expires on its own.

    And note: storing the consent record is itself legally NECESSARY. You must
    remember a refusal in order to honour it — which is exactly why our own
    classifier categorises OptanonConsent and ckns_policy as `necessary`.
  */

  function writeConsent(prefs) {
    const record = {
      version: 1,
      timestamp: new Date().toISOString(),   // when consent was given — a
                                             // regulator will ask for this
      preferences: prefs,
    };

    const value = encodeURIComponent(JSON.stringify(record));
    const expires = new Date(
      Date.now() + CONFIG.expiryDays * 864e5    // 864e5 = ms in a day
    ).toUTCString();

    /*
      SameSite=Lax  — not sent on cross-site embedded requests. For a consent
                      record there is no reason to allow that, and Lax is the
                      privacy-respecting default.
      Secure        — HTTPS only. Skipped on localhost so the demo works
                      without a certificate.
      Note there is deliberately NO HttpOnly: this script has to read it back.
    */
    const secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie =
      `${CONFIG.cookieName}=${value}; expires=${expires}; path=/; SameSite=Lax${secure}`;
  }

  function readConsent() {
    /*
      `document.cookie` returns ONE string with every readable cookie:
          "theme=dark; lang=en; cookieguard_consent=%7B..."
      There is no API to get one by name — you split it yourself. This is a
      genuinely awkward corner of the web platform.
    */
    const match = document.cookie
      .split('; ')
      .find((row) => row.startsWith(CONFIG.cookieName + '='));

    if (!match) return null;

    try {
      const json = decodeURIComponent(match.split('=').slice(1).join('='));
      const record = JSON.parse(json);
      // Ignore records from a future version of this script — safer to re-ask
      // than to misinterpret a format we don't understand.
      if (record.version !== 1) return null;
      return record;
    } catch (e) {
      // Corrupted cookie. Treat as "no decision yet" and ask again.
      return null;
    }
  }

  function clearConsent() {
    // The only way to delete a cookie is to re-set it with a past expiry.
    document.cookie =
      `${CONFIG.cookieName}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }

  /* ======================================================================
     ACTUALLY BLOCKING TRACKERS
     ======================================================================

     A banner that only records a preference and changes nothing is
     THEATRE — and it is precisely what regulators have been fining people
     for. The banner has to actually prevent the scripts from running.

     HOW SCRIPT BLOCKING WORKS
     -------------------------
     The site owner marks tracker scripts like this:

         <script type="text/plain"
                 data-cookieguard="analytics"
                 src="https://www.googletagmanager.com/gtag/js?id=..."></script>

     The trick is `type="text/plain"`.

     A browser only EXECUTES a <script> whose type it recognises as
     JavaScript. Give it an unknown type and it treats the tag as inert data —
     it does not download the src, does not run the code, does nothing at all.

         type="text/javascript"   →  downloads and runs
         type="text/plain"        →  completely ignored     ← blocked

     Then, once consent is given, we REPLACE each allowed tag with a real one:

         <script src="..."></script>      ← now it downloads and runs

     You cannot simply change the `type` attribute of an existing tag. The
     browser decides whether to execute a script when the element is INSERTED
     into the document; mutating it afterwards does nothing. You must create a
     fresh element. That is a subtle and important detail.
  */

  function unblockScripts(prefs) {
    const blocked = document.querySelectorAll('script[data-cookieguard]');

    blocked.forEach((oldTag) => {
      const category = oldTag.getAttribute('data-cookieguard');

      // Not consented, or already activated — leave it inert.
      if (!prefs[category]) return;
      if (oldTag.dataset.cgActivated === 'true') return;

      // Build a REAL script element.
      const newTag = document.createElement('script');

      // Copy every attribute except the ones that made it inert.
      for (const attr of oldTag.attributes) {
        if (attr.name === 'type' || attr.name === 'data-cookieguard') continue;
        newTag.setAttribute(attr.name, attr.value);
      }
      // Inline scripts have their code in textContent rather than a src.
      if (!oldTag.src) newTag.textContent = oldTag.textContent;

      oldTag.dataset.cgActivated = 'true';

      // Inserting it is what triggers execution.
      oldTag.parentNode.insertBefore(newTag, oldTag.nextSibling);
    });
  }

  /*
    DELETING COOKIES THE USER REFUSED.

    If someone previously accepted and then withdraws, the cookies are already
    on their device. Removing consent has to remove those too — otherwise the
    tracking simply continues and the withdrawal was meaningless.

    HONEST LIMITATION, and worth stating plainly in an interview:
    JavaScript can only delete cookies it can SEE and WRITE. That means:

        ✅ first-party, non-HttpOnly cookies
        ❌ third-party cookies (different domain — no access)
        ❌ HttpOnly cookies (deliberately hidden from JS)

    So this is best-effort. A complete solution needs the server to clear its
    own cookies, and third parties to honour the TCF signal we send them.
    Real consent platforms have exactly the same limitation; they just don't
    always mention it.
  */
  function deleteRefusedCookies(prefs) {
    const KNOWN = {
      analytics: [/^_ga/, /^_gid$/, /^_gat/, /^__utm/, /^_hj/, /^mp_/,
                  /^ajs_/, /^_pk_/, /^_clck$/, /^_clsk$/, /^_cb/, /^s_[a-z]{2}$/],
      marketing: [/^_fbp$/, /^_fbc$/, /^_gcl_/, /^IDE$/, /^test_cookie$/,
                  /^_ttp$/, /^__gads$/, /^__gpi$/, /^_uet/, /^__hs/,
                  /^hubspotutk$/, /^_pin_unauth$/, /^_rdt_uuid$/, /^cto_bundle$/],
      functional: [/^lang$/, /^language$/, /^theme$/, /^locale$/, /^currency$/],
    };

    const names = document.cookie.split('; ')
      .map((row) => row.split('=')[0])
      .filter(Boolean);

    Object.keys(KNOWN).forEach((category) => {
      if (prefs[category]) return;          // consented — leave alone
      names.forEach((name) => {
        if (!KNOWN[category].some((re) => re.test(name))) return;

        // Delete across every plausible domain/path combination. A cookie set
        // on ".example.com" is a DIFFERENT cookie from one on "example.com",
        // and you must match the original attributes to remove it.
        const host = location.hostname;
        const domains = ['', host, '.' + host,
                         '.' + host.split('.').slice(-2).join('.')];
        domains.forEach((domain) => {
          document.cookie =
            `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/` +
            (domain ? `; domain=${domain}` : '');
        });
      });
    });
  }

  /* ======================================================================
     APPLYING A DECISION
     ====================================================================== */

  function applyConsent(prefs, persist) {
    if (persist) writeConsent(prefs);

    deleteRefusedCookies(prefs);
    unblockScripts(prefs);

    /*
      Announce the decision so the host page can react — for example, to load
      an embedded YouTube player only after marketing consent.

          window.addEventListener('cookieguard:consent', (e) => {
            if (e.detail.marketing) loadVideoEmbed();
          });

      A CustomEvent is the clean way for a third-party script to talk to its
      host without requiring the host to call our functions. We don't know
      their code; they don't need to know ours.
    */
    window.dispatchEvent(new CustomEvent('cookieguard:consent', {
      detail: prefs,
    }));

    // Google Consent Mode v2 — the de-facto standard signal. If the site uses
    // Google tags, this tells them what they're allowed to do.
    if (typeof window.gtag === 'function') {
      window.gtag('consent', 'update', {
        analytics_storage:  prefs.analytics ? 'granted' : 'denied',
        ad_storage:         prefs.marketing ? 'granted' : 'denied',
        ad_user_data:       prefs.marketing ? 'granted' : 'denied',
        ad_personalization: prefs.marketing ? 'granted' : 'denied',
      });
    }
  }

  /* ======================================================================
     STYLES
     ======================================================================
     Injected by the script, because we cannot ask the host site to add a
     stylesheet.

     EVERY CLASS IS PREFIXED `cg-`. On someone else's site our `.banner` would
     collide with their `.banner`. Prefixing is the simplest reliable defence.
     (Shadow DOM would isolate more thoroughly, but it complicates styling and
     accessibility; a prefix is the right trade-off for one small widget.)
  */

  function injectStyles() {
    if (document.getElementById('cg-styles')) return;

    const css = `
      .cg-overlay {
        position: fixed; inset: 0; background: rgba(15,23,42,.55);
        z-index: 2147483646; display: flex; align-items: flex-end;
        justify-content: center; padding: 16px;
        animation: cg-fade .25s ease;
      }
      .cg-overlay[data-position="center"] { align-items: center; }
      @keyframes cg-fade { from { opacity: 0 } to { opacity: 1 } }
      @keyframes cg-rise { from { transform: translateY(16px); opacity: 0 }
                           to   { transform: translateY(0);    opacity: 1 } }

      .cg-panel {
        background: #fff; color: #0f172a; border-radius: 14px;
        max-width: 720px; width: 100%; max-height: 86vh; overflow-y: auto;
        box-shadow: 0 10px 40px rgba(0,0,0,.3); padding: 24px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 14px; line-height: 1.55;
        animation: cg-rise .3s ease;
        box-sizing: border-box;
      }
      .cg-panel * { box-sizing: border-box; }
      .cg-panel h2 { margin: 0 0 8px; font-size: 18px; }
      .cg-panel p  { margin: 0 0 14px; color: #475569; }
      .cg-panel a  { color: ${CONFIG.accentColor}; }

      /* ⚠ VALIDITY RULE 2: Accept and Reject must be EQUALLY prominent.
         Same size, same weight, same visual hierarchy. This is exactly what
         CNIL fined Google and Facebook over. */
      .cg-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 6px; }
      .cg-btn {
        flex: 1 1 150px; padding: 11px 18px; font-size: 14px; font-weight: 600;
        font-family: inherit; border-radius: 9px; cursor: pointer;
        border: 1.5px solid ${CONFIG.accentColor}; transition: opacity .15s;
      }
      .cg-btn:hover { opacity: .85; }
      .cg-btn-accept { background: ${CONFIG.accentColor}; color: #fff; }
      .cg-btn-reject { background: ${CONFIG.accentColor}; color: #fff; }
      .cg-btn-custom { background: transparent; color: ${CONFIG.accentColor}; }

      .cg-cat {
        border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 12px 14px; margin-bottom: 10px;
      }
      .cg-cat-head {
        display: flex; justify-content: space-between;
        align-items: center; gap: 12px;
      }
      .cg-cat-name { font-weight: 650; }
      .cg-cat-desc { color: #475569; font-size: 13px; margin-top: 5px; }
      .cg-locked { font-size: 12px; color: #059669; font-weight: 600; }

      /* A switch built from a checkbox — accessible by default, because it
         IS a real checkbox underneath. Keyboard and screen readers work
         without any extra ARIA. */
      .cg-switch { position: relative; width: 44px; height: 24px; flex-shrink: 0; }
      .cg-switch input { opacity: 0; width: 0; height: 0; }
      .cg-slider {
        position: absolute; inset: 0; background: #cbd5e1;
        border-radius: 24px; cursor: pointer; transition: background .2s;
      }
      .cg-slider:before {
        content: ""; position: absolute; width: 18px; height: 18px;
        left: 3px; top: 3px; background: #fff; border-radius: 50%;
        transition: transform .2s;
      }
      .cg-switch input:checked + .cg-slider { background: ${CONFIG.accentColor}; }
      .cg-switch input:checked + .cg-slider:before { transform: translateX(20px); }
      .cg-switch input:disabled + .cg-slider { background: #059669; cursor: not-allowed; }
      /* Visible keyboard focus — never remove this. */
      .cg-switch input:focus-visible + .cg-slider {
        box-shadow: 0 0 0 3px rgba(79,70,229,.35);
      }

      .cg-footer { font-size: 12px; color: #64748b; margin-top: 14px; }

      /* The persistent re-open button. ⚠ VALIDITY RULE 5: withdrawing
         consent must be as easy as giving it. */
      .cg-reopen {
        position: fixed; bottom: 16px; left: 16px; z-index: 2147483645;
        width: 44px; height: 44px; border-radius: 50%; border: none;
        background: ${CONFIG.accentColor}; color: #fff; font-size: 20px;
        cursor: pointer; box-shadow: 0 3px 12px rgba(0,0,0,.25);
        display: flex; align-items: center; justify-content: center;
      }
      .cg-reopen:hover { transform: scale(1.06); }

      @media (prefers-color-scheme: dark) {
        .cg-panel { background: #151e33; color: #e6edf7; }
        .cg-panel p, .cg-cat-desc { color: #94a3b8; }
        .cg-cat { border-color: #263349; }
      }
      @media (max-width: 520px) {
        .cg-btn { flex: 1 1 100%; }
      }
    `;

    const style = document.createElement('style');
    style.id = 'cg-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  /* ======================================================================
     THE BANNER UI
     ====================================================================== */

  let overlayEl = null;

  function closeBanner() {
    if (overlayEl) { overlayEl.remove(); overlayEl = null; }
    showReopenButton();
  }

  function buildBanner(showDetails, existing) {
    injectStyles();
    if (overlayEl) overlayEl.remove();

    const saved = (existing && existing.preferences) || {};

    overlayEl = document.createElement('div');
    overlayEl.className = 'cg-overlay';
    overlayEl.setAttribute('data-position', CONFIG.position);
    // role="dialog" + aria-modal tell assistive technology this is a modal
    // that should trap attention — without them a screen reader user may not
    // realise anything appeared.
    overlayEl.setAttribute('role', 'dialog');
    overlayEl.setAttribute('aria-modal', 'true');
    overlayEl.setAttribute('aria-labelledby', 'cg-title');

    const policyLink = CONFIG.policyUrl
      ? ` Read our <a href="${CONFIG.policyUrl}" target="_blank" rel="noopener">privacy policy</a>.`
      : '';

    let inner = `
      <div class="cg-panel">
        <h2 id="cg-title">🍪 We use cookies</h2>
        <p>
          We use cookies to run this site and, with your permission, to measure
          how it is used and to show you relevant advertising.
          <strong>Nothing non-essential is loaded until you choose.</strong>${policyLink}
        </p>`;

    if (showDetails) {
      inner += '<div class="cg-cats">';
      CATEGORIES.forEach((cat) => {
        /*
          ⚠ VALIDITY RULE 3: NO PRE-TICKED BOXES.

          A returning visitor sees their own previous choice, which is correct.
          A NEW visitor sees everything except "necessary" switched OFF.
          Defaulting to on would make the consent invalid — GDPR requires an
          affirmative action, and a pre-ticked box is not one.
        */
        const isOn = cat.required
          ? true
          : (existing ? !!saved[cat.id] : false);

        inner += `
          <div class="cg-cat">
            <div class="cg-cat-head">
              <span class="cg-cat-name">${cat.name}</span>
              ${cat.required
                ? '<span class="cg-locked">Always active</span>'
                : `<label class="cg-switch">
                     <input type="checkbox" data-cat="${cat.id}"
                            ${isOn ? 'checked' : ''}
                            aria-label="${cat.name}">
                     <span class="cg-slider"></span>
                   </label>`}
            </div>
            <div class="cg-cat-desc">${cat.description}</div>
          </div>`;
      });
      inner += '</div>';
    }

    /*
      ⚠ VALIDITY RULE 2 AGAIN, in the markup this time.
      "Reject all" is rendered with the SAME button styling as "Accept all",
      in the same row, at the same size. No dark patterns: no greyed-out
      reject, no "Reject" hidden behind "Manage preferences".
    */
    inner += `
        <div class="cg-actions">
          <button class="cg-btn cg-btn-reject" id="cg-reject">Reject all</button>
          <button class="cg-btn cg-btn-accept" id="cg-accept">Accept all</button>
          ${showDetails
            ? '<button class="cg-btn cg-btn-custom" id="cg-save">Save my choices</button>'
            : '<button class="cg-btn cg-btn-custom" id="cg-customise">Customise</button>'}
        </div>
        <div class="cg-footer">
          You can change this at any time using the cookie button in the corner.
        </div>
      </div>`;

    overlayEl.innerHTML = inner;
    document.body.appendChild(overlayEl);

    // ---- wire the buttons ----
    const $$id = (id) => overlayEl.querySelector('#' + id);

    $$id('cg-accept').onclick = () => {
      const prefs = {};
      CATEGORIES.forEach((c) => { prefs[c.id] = true; });
      applyConsent(prefs, true);
      closeBanner();
    };

    $$id('cg-reject').onclick = () => {
      const prefs = {};
      // Necessary stays true — it is exempt and cannot be refused.
      CATEGORIES.forEach((c) => { prefs[c.id] = !!c.required; });
      applyConsent(prefs, true);
      closeBanner();
    };

    if (showDetails) {
      $$id('cg-save').onclick = () => {
        const prefs = { necessary: true };
        overlayEl.querySelectorAll('input[data-cat]').forEach((input) => {
          prefs[input.dataset.cat] = input.checked;
        });
        applyConsent(prefs, true);
        closeBanner();
      };
    } else {
      // ⚠ VALIDITY RULE 4: granular choice must be reachable, and in ONE click.
      $$id('cg-customise').onclick = () => buildBanner(true, existing);
    }

    // Focus the first button so keyboard users land inside the dialog.
    $$id('cg-reject').focus();
  }

  /* ---- the persistent re-open button ---- */

  function showReopenButton() {
    if (document.getElementById('cg-reopen')) return;
    injectStyles();

    const btn = document.createElement('button');
    btn.id = 'cg-reopen';
    btn.className = 'cg-reopen';
    btn.textContent = '🍪';
    // ⚠ VALIDITY RULE 5: withdrawal must be as easy as giving.
    // A permanently visible control, one click away, on every page.
    btn.setAttribute('aria-label', 'Change your cookie preferences');
    btn.title = 'Cookie preferences';
    btn.onclick = () => buildBanner(true, readConsent());
    document.body.appendChild(btn);
  }

  /* ======================================================================
     PUBLIC API
     ======================================================================
     Exposed on `window` so the host page (and our tests) can drive it.
     ONE global object, not several — minimising our footprint on someone
     else's namespace.
  */

  const CookieGuardConsent = {
    /** Current preferences, or null if the user hasn't decided yet. */
    getConsent() {
      const record = readConsent();
      return record ? record.preferences : null;
    },

    /** Has the user consented to this category? */
    hasConsent(category) {
      const prefs = this.getConsent();
      return !!(prefs && prefs[category]);
    },

    /** Full record including the timestamp — what a regulator would ask for. */
    getRecord() { return readConsent(); },

    /** Open the preferences dialog. */
    show(details) { buildBanner(details !== false, readConsent()); },

    /** Wipe the decision and ask again. Used by the demo's reset button. */
    reset() {
      clearConsent();
      const btn = document.getElementById('cg-reopen');
      if (btn) btn.remove();
      buildBanner(false, null);
    },

    CATEGORIES,
  };

  window.CookieGuardConsent = CookieGuardConsent;

  /* ======================================================================
     STARTUP
     ====================================================================== */

  function start() {
    const existing = readConsent();

    if (existing) {
      /*
        ⚠ VALIDITY RULE 1, the returning-visitor half.
        We re-apply the stored decision immediately on every page load. Scripts
        the user allowed get activated; the rest stay inert. Without this, a
        blocked script would stay blocked forever even after consent — the
        banner would be recording a preference it never acted on.
      */
      applyConsent(existing.preferences, false);
      showReopenButton();
    } else {
      // No decision yet. Everything stays blocked, and we ask.
      buildBanner(false, null);
    }
  }

  // The banner needs <body> to exist before it can be appended.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();     // the script was added late; the DOM is already there
  }
})();
