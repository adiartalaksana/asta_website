document.addEventListener("DOMContentLoaded", () => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(pointer: fine)").matches;

  /* ------------------------------------------------------------ i18n */
  const translations = {
    en: {
      navPlatform: "Services", navGovernance: "Our approach", navCompany: "About us", portal: "Client portal",
      heroEyebrow: "TAX & BUSINESS CONSULTING", heroTitle: "Less tax stress,<br>more room to <em>grow.</em>",
      heroLead: "Asta Asa Abadi helps individuals and businesses understand tax obligations, manage risk, and make more confident decisions.",
      demo: "Book a consultation", explore: "Explore our services", proofAssurance: "expert guidance", proofLongTerm: "long-term partnership", scroll: "Scroll",
      platformLabel: "OUR SERVICES / 01", platformTitle: "Clearer tax.<br><em>More confident steps.</em>",
      platformCopy: "Practical support to meet obligations, anticipate change, and keep your business moving with confidence.",
      journalingTitle: "Tax<br>Advisory", journalingCopy: "Relevant tax advice for business decisions, investments, and your personal needs.", openLedger: "Discuss your needs",
      classificationTitle: "Tax<br>Compliance", classificationCopy: "Reporting and compliance guidance that lets you focus on running your business.", seeArchitecture: "Find a solution",
      assuranceTitle: "Business<br>Support", assuranceCopy: "Insight to help you see opportunities, manage risk, and grow sustainably.", exploreGovernance: "Talk to us",
      governanceLabel: "GUIDANCE THAT MATTERS / 02",
      governanceTitle: "Tax is more than<br>numbers. It is about<br><em>your future.</em>",
      governanceCopy: "We simplify complexity so you have more room to build the business and life you want.",
      immutableDefault: "Easy-to-understand language", immutableCopy: "We turn tax rules into clear, practical next steps.",
      privateArchitecture: "Confidentiality protected", privateCopy: "Your information is always handled professionally and with care.",
      evidenceAccountability: "Responsive support", evidenceCopy: "We are here when you need clear direction.",
      aboutLabel: "ABOUT ASTA ASA ABADI / 03",
      companyStatement: "We believe tax should not stand in the way of your ambition. Asta Asa Abadi is a consulting partner for individuals and businesses ready to move forward with confidence.",
      discover: "Discover our approach", ctaEyebrow: "START WITH A CONVERSATION",
      ctaTitle: "Let’s make tax<br>more <em>straightforward.</em>", footerStatement: "Personal tax consulting<br>for more confident business steps.",
      accessLedger: "Access client portal",
      loginEyebrow: "SECURE PORTAL", loginTitle: "Sign in to client portal", loginIntro: "Use your company account to continue.",
      username: "Username", password: "Password", login: "Sign in securely",
    },
  };
  // The markup is authored in Indonesian; capture it so switching back from English restores it.
  translations.id = {};
  document.querySelectorAll("[data-i18n]").forEach((el) => { translations.id[el.dataset.i18n] ??= el.textContent; });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => { translations.id[el.dataset.i18nHtml] ??= el.innerHTML; });

  const storage = {
    get(key) { try { return window.localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
  };

  // Hero headline: wrap each <br>-separated line so it can rise into view.
  const splitHeroTitle = () => {
    const title = document.querySelector(".hero-title");
    if (!title) return;
    title.innerHTML = title.innerHTML
      .split(/<br\s*\/?>/i)
      .map((line, i) => `<span class="line"><span style="--i:${i}">${line.trim()}</span></span>`)
      .join("");
  };
  // Statement: wrap words so they light up while scrolling.
  const splitWords = () => {
    document.querySelectorAll("[data-words]").forEach((el) => {
      el.innerHTML = el.textContent.trim().split(/\s+/).map((word) => `<span class="word">${word}</span>`).join(" ");
    });
  };

  const languageToggle = document.querySelector(".language-toggle");
  const setLanguage = (language) => {
    const dictionary = translations[language] || translations.id;
    document.documentElement.lang = translations[language] ? language : "id";
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const value = dictionary[el.dataset.i18n];
      if (value) el.textContent = value;
    });
    document.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const value = dictionary[el.dataset.i18nHtml];
      if (value) el.innerHTML = value;
    });
    splitHeroTitle();
    splitWords();
    if (languageToggle) {
      languageToggle.textContent = language === "en" ? "ID" : "EN";
      languageToggle.setAttribute("aria-label", language === "en" ? "Ganti ke Bahasa Indonesia" : "Switch to English");
    }
    window.aaaTheme?.refreshLabels();
    requestScrollUpdate();
  };

  /* ------------------------------------------------------------ login modal */
  const loginModal = document.querySelector(".login-modal");
  let loginTrigger;
  const openLogin = (trigger) => {
    loginTrigger = trigger;
    loginModal?.classList.add("is-open");
    loginModal?.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    setTimeout(() => loginModal?.querySelector("input[name='username']")?.focus(), 60);
  };
  const closeLogin = () => {
    loginModal?.classList.remove("is-open");
    loginModal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    if (window.location.hash === "#portal-login") history.replaceState(null, "", window.location.pathname + window.location.search);
    loginTrigger?.focus();
  };
  document.querySelectorAll("[data-login-modal]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      closeMenu();
      history.replaceState(null, "", "#portal-login");
      openLogin(trigger);
    });
  });
  loginModal?.querySelectorAll("[data-close-login]").forEach((control) => control.addEventListener("click", closeLogin));

  /* ------------------------------------------------------------ mobile menu */
  const toggle = document.querySelector(".menu-toggle");
  const menu = document.querySelector(".mobile-menu");
  function closeMenu() {
    menu?.classList.remove("is-open");
    toggle?.classList.remove("is-open");
    toggle?.setAttribute("aria-expanded", "false");
    if (!loginModal?.classList.contains("is-open")) document.body.classList.remove("modal-open");
  }
  toggle?.addEventListener("click", () => {
    const isOpen = menu?.classList.toggle("is-open");
    toggle.classList.toggle("is-open", isOpen);
    toggle.setAttribute("aria-expanded", String(isOpen));
    document.body.classList.toggle("modal-open", isOpen);
  });
  menu?.querySelectorAll("a:not([data-login-modal])").forEach((link) => link.addEventListener("click", closeMenu));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (loginModal?.classList.contains("is-open")) closeLogin();
    if (menu?.classList.contains("is-open")) closeMenu();
  });

  /* ------------------------------------------------------------ scroll-driven effects */
  const header = document.querySelector(".site-header");
  const progress = document.querySelector(".scroll-progress");
  const watermark = document.querySelector(".governance-watermark");
  const ctaSection = document.querySelector(".final-cta");
  const navLinks = [...document.querySelectorAll(".primary-nav a")];
  const sections = navLinks.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));

  const updateWords = () => {
    document.querySelectorAll("[data-words]").forEach((el) => {
      const words = el.querySelectorAll(".word");
      if (reduceMotion) { words.forEach((w) => w.classList.add("is-lit")); return; }
      const rect = el.getBoundingClientRect();
      const p = clamp((window.innerHeight * 0.85 - rect.top) / (rect.height + window.innerHeight * 0.45));
      const lit = Math.round(p * words.length);
      words.forEach((word, i) => word.classList.toggle("is-lit", i < lit));
    });
  };

  const onScroll = () => {
    ticking = false;
    const y = window.scrollY;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    progress?.style.setProperty("--progress", (y / Math.max(max, 1)).toFixed(4));
    header?.classList.toggle("is-scrolled", y > 24);
    updateWords();
    if (!reduceMotion && watermark) {
      const rect = watermark.parentElement.getBoundingClientRect();
      watermark.style.setProperty("--parallax", `${(rect.top + rect.height / 2 - window.innerHeight / 2) * -0.18}px`);
    }
    if (!reduceMotion && ctaSection) {
      const rect = ctaSection.getBoundingClientRect();
      ctaSection.style.setProperty("--cta-rot", ((window.innerHeight - rect.top) * 0.03).toFixed(2));
    }
    let current = null;
    sections.forEach((section, i) => { if (section.getBoundingClientRect().top < window.innerHeight * 0.4) current = i; });
    navLinks.forEach((link, i) => link.classList.toggle("is-current", i === current));
  };
  let ticking = false;
  function requestScrollUpdate() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(onScroll);
  }
  window.addEventListener("scroll", requestScrollUpdate, { passive: true });
  window.addEventListener("resize", requestScrollUpdate);

  /* ------------------------------------------------------------ reveal on scroll */
  const revealItems = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduceMotion) {
    const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }), { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
    revealItems.forEach((item) => {
      const siblings = [...item.parentElement.children].filter((el) => el.classList.contains("reveal"));
      item.style.setProperty("--delay", `${siblings.indexOf(item) * 0.12}s`);
      observer.observe(item);
    });
  } else {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }

  /* ------------------------------------------------------------ pointer effects */
  if (finePointer && !reduceMotion) {
    document.querySelectorAll("[data-spotlight]").forEach((area) => {
      area.addEventListener("pointermove", (event) => {
        const rect = area.getBoundingClientRect();
        area.style.setProperty("--mx", `${event.clientX - rect.left}px`);
        area.style.setProperty("--my", `${event.clientY - rect.top}px`);
      });
    });

    const hero = document.querySelector(".hero");
    const tilt = document.querySelector(".mark-tilt");
    hero?.addEventListener("pointermove", (event) => {
      const x = event.clientX / window.innerWidth - 0.5;
      const y = event.clientY / window.innerHeight - 0.5;
      tilt?.style.setProperty("--ry", `${x * 26}deg`);
      tilt?.style.setProperty("--rx", `${y * -22}deg`);
    });
    hero?.addEventListener("pointerleave", () => {
      tilt?.style.setProperty("--ry", "0deg");
      tilt?.style.setProperty("--rx", "0deg");
    });

    document.querySelectorAll(".service-card").forEach((card) => {
      card.addEventListener("pointermove", (event) => {
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--ry", `${((event.clientX - rect.left) / rect.width - 0.5) * 8}deg`);
        card.style.setProperty("--rx", `${((event.clientY - rect.top) / rect.height - 0.5) * -8}deg`);
      });
      card.addEventListener("pointerleave", () => {
        card.style.setProperty("--ry", "0deg");
        card.style.setProperty("--rx", "0deg");
      });
    });

    document.querySelectorAll(".magnetic").forEach((button) => {
      button.addEventListener("pointermove", (event) => {
        const rect = button.getBoundingClientRect();
        const dx = event.clientX - rect.left - rect.width / 2;
        const dy = event.clientY - rect.top - rect.height / 2;
        button.style.transform = `translate(${dx * 0.18}px, ${dy * 0.28}px)`;
      });
      button.addEventListener("pointerleave", () => { button.style.transform = ""; });
    });
  }

  /* ------------------------------------------------------------ hero growth pulse (canvas) */
  const canvas = document.querySelector(".pulse-canvas");
  if (canvas && !reduceMotion && canvas.getContext) {
    const ctx = canvas.getContext("2d");
    let width = 0; let height = 0; let running = true; let start = performance.now();
    let glow = "168,198,226"; let ink = "220,232,244";
    const readColors = () => {
      const styles = getComputedStyle(document.documentElement);
      glow = styles.getPropertyValue("--glow-rgb").trim() || glow;
      ink = styles.getPropertyValue("--ink-rgb").trim() || ink;
    };
    readColors();
    window.addEventListener("aaa:themechange", () => requestAnimationFrame(readColors));
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth; height = canvas.clientHeight;
      canvas.width = width * dpr; canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    // One period echoes the S of the logo: flat, a sharp rise, a dip, then a higher plateau.
    const shape = (u) => {
      if (u < 0.42) return 0;
      if (u < 0.5) return -((u - 0.42) / 0.08) * 1.0;
      if (u < 0.56) return -1 + ((u - 0.5) / 0.06) * 1.55;
      if (u < 0.62) return 0.55 - ((u - 0.56) / 0.06) * 0.75;
      return -0.2 * (1 - (u - 0.62) / 0.38);
    };
    const draw = (now) => {
      if (!running) return;
      const t = (now - start) / 1000;
      ctx.clearRect(0, 0, width, height);
      const period = Math.max(width * 0.55, 420);
      const offset = (t * 70) % period;
      const lines = [
        { base: height * 0.72, amp: Math.min(height * 0.16, 120), alpha: 0.5, widthPx: 1.6, shift: 0 },
        { base: height * 0.8, amp: Math.min(height * 0.08, 60), alpha: 0.16, widthPx: 1, shift: period * 0.37 },
      ];
      lines.forEach((line) => {
        const gradient = ctx.createLinearGradient(0, 0, width, 0);
        gradient.addColorStop(0, `rgba(${glow},0)`);
        gradient.addColorStop(0.35, `rgba(${glow},${line.alpha})`);
        gradient.addColorStop(1, `rgba(${glow},0)`);
        ctx.beginPath();
        for (let x = 0; x <= width; x += 3) {
          const u = (((x + offset + line.shift) % period) + period) % period / period;
          const y = line.base + shape(u) * line.amp;
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = gradient;
        ctx.lineWidth = line.widthPx;
        ctx.shadowColor = `rgba(${glow},.6)`;
        ctx.shadowBlur = line.alpha > 0.3 ? 12 : 0;
        ctx.stroke();
      });
      // Faint dotted grid for depth.
      ctx.shadowBlur = 0;
      ctx.fillStyle = `rgba(${ink},.07)`;
      for (let x = (-offset * 0.25) % 48; x < width; x += 48) {
        for (let y = 24; y < height; y += 48) ctx.fillRect(x, y, 1.2, 1.2);
      }
      requestAnimationFrame(draw);
    };
    resize();
    window.addEventListener("resize", resize);
    const visibility = new IntersectionObserver(([entry]) => {
      const shouldRun = entry.isIntersecting && !document.hidden;
      if (shouldRun && !running) { running = true; start = performance.now() - 0; requestAnimationFrame(draw); }
      if (!shouldRun) running = false;
    });
    visibility.observe(canvas);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) running = false;
      else if (!running) { running = true; requestAnimationFrame(draw); }
    });
    requestAnimationFrame(draw);
  }

  // Language follows the browser setting until the visitor chooses one with the toggle.
  // (A new storage key: the old one was written automatically on every visit.)
  const LANGUAGE_KEY = "aaa-language-choice";
  const browserLanguage = () => {
    const preferred = navigator.languages?.length ? navigator.languages : [navigator.language || "id"];
    for (const tag of preferred) {
      const code = String(tag).toLowerCase().split("-")[0];
      if (code === "id" || code === "in") return "id";
      if (code === "en") return "en";
    }
    return "en"; // other languages: English is the closest shared language
  };
  const chosenLanguage = () => {
    const value = storage.get(LANGUAGE_KEY);
    return value === "id" || value === "en" ? value : null;
  };
  setLanguage(chosenLanguage() || browserLanguage());
  languageToggle?.addEventListener("click", () => {
    const next = document.documentElement.lang === "en" ? "id" : "en";
    storage.set(LANGUAGE_KEY, next);
    setLanguage(next);
  });
  window.addEventListener("languagechange", () => {
    if (!chosenLanguage()) setLanguage(browserLanguage());
  });
  if (window.location.hash === "#portal-login") openLogin();
  window.addEventListener("hashchange", () => { if (window.location.hash === "#portal-login") openLogin(); });
  onScroll();
});
