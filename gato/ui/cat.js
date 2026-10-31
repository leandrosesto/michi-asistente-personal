// Gato "Gomita": cuerpo de pastilla bien redondo, orejas suaves y ojos grandes con pupila rasgada. Plano y sin boca.
// El aspecto sale de un skin (forma, pelaje, patrón y color de cada ojo); ver skins.js.
(function () {
  function hexToRgb(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) {
    const A = hexToRgb(a), B = hexToRgb(b);
    return 'rgb(' + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(',') + ')';
  }
  function rgba(h, a) {
    const [r, g, b] = hexToRgb(h);
    return `rgba(${r},${g},${b},${a})`;
  }
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  // Área con signo (fórmula del cordón); positiva = sentido horario en pantalla
  const area = (pts) => pts.reduce((s, p, i) => {
    const q = pts[(i + 1) % pts.length];
    return s + p.x * q.y - q.x * p.y;
  }, 0);

  // Lienzo lógico de 120x110
  const W = 120, H = 110;
  const SHAPES = {
    gomita: {
      body: { x0: 12, y0: 36, x1: 108, y1: 102, r: 36 },
      ear: { ib: { x: 46, y: 42 }, ob: { x: 16, y: 60 }, tip: { x: 22, y: 16 }, r: 9 },
      earIn: { ib: { x: 40, y: 44 }, ob: { x: 24, y: 53 }, tip: { x: 26, y: 28 }, r: 5 },
      eyeY: 72, eyeDX: 20, eyeR: 11,
    },
    // Más ancho y petiso, orejas chicas y bajas, con mechones de pelo a los costados y abajo
    persa: {
      body: { x0: 9, y0: 42, x1: 111, y1: 102, r: 40 },
      ear: { ib: { x: 44, y: 47 }, ob: { x: 17, y: 60 }, tip: { x: 24, y: 29 }, r: 8 },
      earIn: { ib: { x: 39, y: 48 }, ob: { x: 25, y: 55 }, tip: { x: 28, y: 37 }, r: 4 },
      eyeY: 75, eyeDX: 21, eyeR: 11.5, fluff: true,
    },
  };
  const DEFAULT_SKIN = {
    forma: 'gomita', pelaje: '#141414', patron: 'liso', color_patron: '#2c2c2c', color_patron2: '#2c2c2c',
    ojo_izq: '#ffcc00', ojo_der: '#ffcc00',
  };

  // Valores objetivo por estado; el dibujo interpola suavemente hacia ellos
  const STATES = {
    idle:      { open: 1.0,  dil: 0.22, earUp: 0.0,  glow: 0.35, ring: 0, happy: 0 },
    listening: { open: 1.2,  dil: 0.55, earUp: 1.0,  glow: 0.7,  ring: 1, happy: 0 },
    thinking:  { open: 0.6,  dil: 0.28, earUp: 0.4,  glow: 0.45, ring: 0, happy: 0 },
    speaking:  { open: 1.0,  dil: 0.3,  earUp: 0.3,  glow: 0.45, ring: 0, happy: 0 },
    happy:     { open: 1.0,  dil: 0.3,  earUp: 0.6,  glow: 0.55, ring: 0, happy: 1 },
    sad:       { open: 0.5,  dil: 0.4,  earUp: -1.0, glow: 0.2,  ring: 0, happy: 0 },
  };

  class Cat {
    constructor(canvas, opts = {}) {
      this.c = canvas;
      this.ctx = canvas.getContext('2d');
      this.setSkin(opts.skin);
      this.state = 'idle';
      this.v = { ...STATES.idle, lookX: 0, lookY: 0, twL: 0, twR: 0 };
      this.amp = 0;
      this.ampS = 0;
      this.ext = { x: 0, y: 0, at: 0 };
      this.wander = { x: 0, y: 0, next: 0 };
      this.blinkAt = -1;
      this.hopAt = -1;
      this.nextBlink = performance.now() + 2000;
      this.nextTwitch = performance.now() + 6000;
      this.last = performance.now();
      this.resize();
      window.addEventListener('resize', () => this.resize());
      requestAnimationFrame(t => this.frame(t));
    }

    resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = this.c.clientWidth, h = this.c.clientHeight;
      this.c.width = Math.round(w * dpr);
      this.c.height = Math.round(h * dpr);
      this.ctx.setTransform(dpr * w / W, 0, 0, dpr * h / H, 0, 0);
    }

    setSkin(skin) {
      this.skin = { ...DEFAULT_SKIN, ...(skin || {}) };
      this.g = SHAPES[this.skin.forma] || SHAPES.gomita;
    }
    setState(s) {
      if (!STATES[s]) return;
      if (s === 'happy' && this.state !== 'happy') this.hopAt = performance.now();
      this.state = s;
    }
    setAmp(a) { this.amp = clamp(+a || 0, 0, 1); }
    // Vector de mirada normalizado (-1..1) calculado desde la posición global del mouse
    setLook(x, y) { this.ext = { x: clamp(x, -1, 1), y: clamp(y, -1, 1), at: performance.now() }; }

    frame(now) {
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      const k = 1 - Math.exp(-dt * 10);
      this.ampS += (this.amp - this.ampS) * (1 - Math.exp(-dt * 18));

      const tgt = { ...STATES[this.state] };
      if (this.state === 'speaking') {
        tgt.glow += this.ampS * 0.55;
        tgt.dil += this.ampS * 0.25;
      } else if (this.state === 'listening') {
        tgt.glow += this.ampS * 0.35;
      } else if (this.state === 'thinking') {
        tgt.glow += 0.12 * Math.sin(now / 300);
      }

      let lx, ly;
      if (this.state === 'thinking') {
        lx = 0.55 * Math.sin(now / 800); ly = -0.8;
      } else if (now - this.ext.at < 3000) {
        lx = this.ext.x; ly = this.ext.y;
      } else {
        if (now > this.wander.next) {
          this.wander = { x: (Math.random() - 0.5) * 1.2, y: (Math.random() - 0.5) * 0.8, next: now + 1500 + Math.random() * 3500 };
        }
        lx = this.wander.x; ly = this.wander.y;
      }
      tgt.lookX = lx; tgt.lookY = ly;

      if (now > this.nextTwitch && (this.state === 'idle' || this.state === 'listening')) {
        if (Math.random() < 0.5) this.v.twL = 0.35; else this.v.twR = 0.35;
        this.nextTwitch = now + 3000 + Math.random() * 7000;
      }
      tgt.twL = 0; tgt.twR = 0;

      for (const key of Object.keys(tgt)) {
        const speed = key === 'twL' || key === 'twR' ? (1 - Math.exp(-dt * 7)) : k;
        this.v[key] += (tgt[key] - this.v[key]) * speed;
      }

      if (now > this.nextBlink && this.blinkAt < 0) {
        this.blinkAt = now;
        this.nextBlink = now + (Math.random() < 0.2 ? 250 : 2200 + Math.random() * 4500);
      }
      let blink = 1;
      if (this.blinkAt >= 0) {
        const p = (now - this.blinkAt) / 140;
        if (p >= 1) this.blinkAt = -1; else blink = 1 - Math.sin(Math.PI * p);
      }

      this.draw(now, blink);
      requestAnimationFrame(t => this.frame(t));
    }

    earPoints(side, shape) {
      // side -1 izquierda, 1 derecha (espejado sobre el centro)
      const m = (p) => ({ x: side < 0 ? p.x : W - p.x, y: p.y });
      return { ib: m(shape.ib), ob: m(shape.ob), tip: m(shape.tip), r: shape.r };
    }

    earTransform(side) {
      const ctx = this.ctx, v = this.v;
      const p = this.earPoints(side, this.g.ear);
      const px = (p.ib.x + p.ob.x) / 2, py = (p.ib.y + p.ob.y) / 2;
      const tw = side < 0 ? v.twL : v.twR;
      // earUp > 0: orejas paradas; < 0: caídas hacia afuera
      const ang = side * (0.08 * v.earUp - 0.38 * Math.max(0, -v.earUp)) * -1 + side * tw * -0.6;
      ctx.translate(px, py);
      ctx.rotate(ang);
      ctx.scale(1, 1 + 0.07 * Math.max(0, v.earUp));
      ctx.translate(-px, -py);
    }

    earPath(side, shape) {
      // Todas las piezas giran en sentido horario (como el cuerpo): si no, al unirlas en un solo camino
      // la regla "nonzero" deja un hueco donde se superponen
      const ctx = this.ctx;
      let { ib, ob, tip, r } = this.earPoints(side, shape || this.g.ear);
      if (area([ib, tip, ob]) < 0) [ib, ob] = [ob, ib];
      ctx.moveTo(ib.x, ib.y);
      ctx.arcTo(tip.x, tip.y, ob.x, ob.y, r);
      ctx.lineTo(ob.x, ob.y);
      ctx.closePath();
    }
    squircle(x0, y0, x1, y1, r) {
      const ctx = this.ctx, q = r * 0.08;
      ctx.moveTo(x0 + r, y0);
      ctx.lineTo(x1 - r, y0);
      ctx.bezierCurveTo(x1 - q, y0, x1, y0 + q, x1, y0 + r);
      ctx.lineTo(x1, y1 - r);
      ctx.bezierCurveTo(x1, y1 - q, x1 - q, y1, x1 - r, y1);
      ctx.lineTo(x0 + r, y1);
      ctx.bezierCurveTo(x0 + q, y1, x0, y1 - q, x0, y1 - r);
      ctx.lineTo(x0, y0 + r);
      ctx.bezierCurveTo(x0, y0 + q, x0 + q, y0, x0 + r, y0);
      ctx.closePath();
    }

    fluffPath() {
      // Pelo del persa: bollitos redondos que asoman por los costados y abajo, como una nube
      const ctx = this.ctx, b = this.g.body;
      const puff = (x, y, r) => { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, Math.PI * 2); };
      for (const side of [-1, 1]) {
        const x = side < 0 ? b.x0 + 2 : b.x1 - 2;
        for (const [y, r] of [[70, 5], [80, 6], [90, 5.5]]) puff(x, y, r);
      }
      for (const [x, r] of [[34, 5.5], [47, 6], [60, 6.5], [73, 6], [86, 5.5]]) puff(x, b.y1 - 1, r);
    }

    // Arma toda la silueta (orejas + cuerpo + mechones) como un solo camino, para pintarla o recortar con ella
    silhouettePath() {
      const ctx = this.ctx, b = this.g.body;
      ctx.beginPath();
      for (const side of [-1, 1]) {
        ctx.save(); this.earTransform(side); this.earPath(side); ctx.restore();
      }
      this.squircle(b.x0, b.y0, b.x1, b.y1, b.r);
      if (this.g.fluff) this.fluffPath();
    }

    fillEar(side, color, inner) {
      const ctx = this.ctx;
      ctx.save(); this.earTransform(side);
      ctx.fillStyle = color;
      ctx.beginPath(); this.earPath(side, inner ? this.g.earIn : this.g.ear); ctx.fill();
      ctx.restore();
    }

    drawPattern() {
      const ctx = this.ctx, s = this.skin, b = this.g.body;
      const blob = (x, y, r, color) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); };
      ctx.save();
      this.silhouettePath();
      ctx.clip();
      switch (s.patron) {
        case 'atigrado': {
          ctx.fillStyle = s.color_patron;
          for (const [dx, len] of [[-11, 7], [0, 10], [11, 7]]) {
            ctx.beginPath(); ctx.ellipse(W / 2 + dx, b.y0 + 4 + len / 2, 2.6, len, 0, 0, Math.PI * 2); ctx.fill();
          }
          for (const side of [-1, 1]) {
            const x = side < 0 ? b.x0 : b.x1;
            for (let i = 0; i < 2; i++) {
              ctx.beginPath(); ctx.ellipse(x, 74 + i * 11, 9, 2.4, side * 0.15, 0, Math.PI * 2); ctx.fill();
            }
          }
          break;
        }
        case 'siames': {
          for (const side of [-1, 1]) this.fillEar(side, s.color_patron);
          const cy = this.g.eyeY + 6;
          const g = ctx.createRadialGradient(W / 2, cy, 4, W / 2, cy, 40);
          g.addColorStop(0, rgba(s.color_patron, 0.9));
          g.addColorStop(0.45, rgba(s.color_patron, 0.55));
          g.addColorStop(1, rgba(s.color_patron, 0));
          ctx.fillStyle = g;
          ctx.beginPath(); ctx.ellipse(W / 2, cy, 44, 32, 0, 0, Math.PI * 2); ctx.fill();
          break;
        }
        case 'calico':
          this.fillEar(-1, s.color_patron);
          this.fillEar(1, s.color_patron2);
          for (const [x, y, r] of [[26, 44, 17], [40, 38, 11], [17, 60, 11]]) blob(x, y, r, s.color_patron);
          for (const [x, y, r] of [[94, 90, 15], [104, 76, 11], [80, 102, 10]]) blob(x, y, r, s.color_patron2);
          break;
        case 'esmoquin':
          ctx.fillStyle = s.color_patron;
          ctx.beginPath(); ctx.moveTo(W / 2, b.y0 + 10); ctx.lineTo(W / 2 - 8, 92); ctx.lineTo(W / 2 + 8, 92); ctx.closePath(); ctx.fill();
          ctx.beginPath(); ctx.ellipse(W / 2, b.y1 - 4, 28, 17, 0, 0, Math.PI * 2); ctx.fill();
          break;
        case 'carey':
          // Manchas irregulares: varios círculos chicos superpuestos en vez de lunares sueltos
          for (const [x, y, r, c] of [[22, 46, 7, 0], [30, 42, 5, 0], [18, 54, 4, 0], [44, 40, 4, 1], [88, 44, 7, 0],
                                      [96, 50, 5, 0], [102, 66, 5, 1], [26, 92, 6, 1], [33, 97, 4, 1], [62, 101, 6, 0],
                                      [70, 98, 4, 0], [94, 94, 6, 0], [14, 76, 4, 0], [72, 52, 3, 1]]) {
            blob(x, y, r, c ? s.color_patron2 : s.color_patron);
          }
          this.fillEar(1, s.color_patron);
          break;
      }
      ctx.restore();
    }

    drawEye(x, y, blink, eye) {
      const ctx = this.ctx, v = this.v;
      const r = this.g.eyeR;
      const h = r * v.open * blink;
      ctx.save();
      ctx.translate(x, y);
      // En reposo los ojos son planos; brillan al escuchar y al hablar
      const glow = Math.max(0, v.glow - 0.38) * 30;

      if (v.happy > 0.5) {
        ctx.shadowColor = eye; ctx.shadowBlur = glow;
        ctx.strokeStyle = eye; ctx.lineWidth = 3.5; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(-r * 0.8, 3); ctx.quadraticCurveTo(0, -9, r * 0.8, 3); ctx.stroke();
        ctx.restore();
        return;
      }
      if (h < 1) {
        ctx.strokeStyle = rgba(eye, 0.85); ctx.lineWidth = 2.2; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(-r * 0.9, 0); ctx.quadraticCurveTo(0, 2.5, r * 0.9, 0); ctx.stroke();
        ctx.restore();
        return;
      }

      const ball = () => { ctx.beginPath(); ctx.ellipse(0, 0, r, h, 0, 0, Math.PI * 2); };
      const px = v.lookX * r * 0.32, py = v.lookY * Math.min(h, r) * 0.3;

      ctx.shadowColor = eye;
      ctx.shadowBlur = glow;
      ctx.fillStyle = eye;
      ball(); ctx.fill();
      ctx.shadowBlur = 0;

      ctx.save();
      ball(); ctx.clip();
      ctx.fillStyle = '#0a0a0a';
      ctx.beginPath(); ctx.ellipse(px, py, Math.max(1.2, r * v.dil), Math.min(h * 1.05, r + 1), 0, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.beginPath(); ctx.arc(px + r * 0.32, py - r * 0.38, 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(px - r * 0.3, py + r * 0.35, 1.1, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      ctx.restore();
    }

    draw(now, blink) {
      const ctx = this.ctx, v = this.v, s = this.skin, b = this.g.body;
      ctx.clearRect(0, 0, W, H);
      const breathe = Math.sin(now / 700);
      let lift = this.state === 'speaking' ? -this.ampS * 2 : 0;
      if (this.hopAt >= 0) {
        const t = (now - this.hopAt) / 380;
        if (t >= 1) this.hopAt = -1; else lift -= 7 * Math.sin(Math.PI * t);
      }

      ctx.save();
      ctx.translate(W / 2, b.y1 + lift);
      ctx.scale(1 + 0.012 * breathe, 1 - 0.015 * breathe);
      ctx.translate(-W / 2, -b.y1);

      if (v.ring > 0.02) {
        const g = 4 + 1.5 * Math.sin(now / 220) + this.ampS * 4;
        ctx.strokeStyle = rgba(s.ojo_izq, 0.45 * v.ring); ctx.lineWidth = 2;
        ctx.beginPath(); this.squircle(b.x0 - g, b.y0 - g, b.x1 + g, b.y1 + g, b.r + g); ctx.stroke();
      }

      // Silueta plana; un halo apenas visible del color de los ojos la despega del fondo
      ctx.fillStyle = s.pelaje;
      ctx.shadowColor = rgba(s.ojo_izq, 0.22);
      ctx.shadowBlur = 8;
      this.silhouettePath(); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fill();

      this.drawPattern();

      const earBase = s.patron === 'siames' ? s.color_patron : s.pelaje;
      for (const side of [-1, 1]) this.fillEar(side, mix(earBase, '#ff9db0', 0.25), true);

      // Brillo tipo gomita sobre la frente
      ctx.fillStyle = mix(s.pelaje, '#ffffff', 0.05);
      ctx.beginPath(); ctx.ellipse(W / 2, b.y0 + 10, 22, 4.5, 0, 0, Math.PI * 2); ctx.fill();

      // Los ojos se desplazan un poco con la mirada, como sobre una esfera
      const ox = v.lookX * 4, oy = v.lookY * 3;
      this.drawEye(W / 2 - this.g.eyeDX + ox, this.g.eyeY + oy, blink, s.ojo_izq);
      this.drawEye(W / 2 + this.g.eyeDX + ox, this.g.eyeY + oy, blink, s.ojo_der);
      ctx.restore();

      if (this.state === 'thinking') {
        for (let i = 0; i < 3; i++) {
          const a = 0.25 + 0.75 * Math.max(0, Math.sin(now / 250 - i * 0.9));
          ctx.fillStyle = rgba(s.ojo_izq, a);
          ctx.beginPath(); ctx.arc(98 + i * 6, 24 - i * 6, 1.8 + i * 0.7, 0, Math.PI * 2); ctx.fill();
        }
      }
    }
  }

  window.Cat = Cat;
})();
