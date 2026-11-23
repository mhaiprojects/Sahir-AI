/* Hands-free / Live voice: PCM capture → /ws/voice (server Silero VAD, router, TTS). */
(() => {
  "use strict";

  function wsModeFromChatMode(mode) {
    if (mode === "stream") return "chat";
    if (mode === "route") return "route";
    return "agent";
  }

  class SahirVoiceClient {
    constructor(hooks = {}) {
      this.hooks = hooks;
      this.ws = null;
      this.ctx = null;
      this.stream = null;
      this.node = null;
      this.mute = null;
      this.playing = [];
      this.playCtx = null;
      this.playHead = 0;
      this._active = false;
      this._starting = false;
    }

    isActive() {
      return this._active && this.ws && this.ws.readyState <= 1;
    }

    stopPlayback() {
      this.playing.forEach((src) => {
        try {
          src.stop();
        } catch (_) {
          /* ignore */
        }
      });
      this.playing = [];
      if (this.playCtx) this.playHead = this.playCtx.currentTime;
    }

    async playWav(buf) {
      this.playCtx = this.playCtx || new AudioContext();
      const audio = await this.playCtx.decodeAudioData(buf.slice(0));
      const src = this.playCtx.createBufferSource();
      src.buffer = audio;
      src.connect(this.playCtx.destination);
      const start = Math.max(this.playCtx.currentTime, this.playHead);
      src.start(start);
      this.playHead = start + audio.duration;
      this.playing.push(src);
      src.onended = () => {
        this.playing = this.playing.filter((x) => x !== src);
      };
    }

    interrupt() {
      this.stopPlayback();
      if (this.ws && this.ws.readyState === 1) {
        this.ws.send(JSON.stringify({ type: "interrupt" }));
      }
    }

    async reconnect() {
      if (!this._active) return;
      const wasActive = true;
      this.stop("reconnect");
      if (wasActive) await this.start();
    }

    async start() {
      if (this._starting || this.isActive()) return;
      this._starting = true;
      try {
        this.hooks.onBeforeStart?.();
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${proto}//${location.host}/ws/voice`);
        this.ws.binaryType = "arraybuffer";

        await new Promise((resolve, reject) => {
          const t = setTimeout(() => reject(new Error("WebSocket connect timeout")), 15000);
          this.ws.onopen = () => {
            clearTimeout(t);
            resolve();
          };
          this.ws.onerror = () => {
            clearTimeout(t);
            reject(new Error("WebSocket failed"));
          };
        });

        this.ws.onclose = () => {
          const was = this._active;
          this._teardownAudio();
          this._active = false;
          if (was) this.hooks.onStop?.("closed");
        };

        this.ws.onmessage = async (ev) => {
          if (typeof ev.data !== "string") {
            await this.playWav(ev.data);
            return;
          }
          let m;
          try {
            m = JSON.parse(ev.data);
          } catch {
            return;
          }
          const t = m.type;
          if (t === "ready") {
            this._active = true;
            this.hooks.onState?.("listening", m);
          } else if (t === "speech_start") {
            this.hooks.onState?.("hearing you", m);
          } else if (t === "barge_in") {
            this.stopPlayback();
            this.hooks.onBargeIn?.(m);
          } else if (t === "status") {
            this.hooks.onState?.(m.state || "listening", m);
          } else if (t === "transcript_partial") {
            if (m.text) this.hooks.onPartial?.(m.text, m);
          } else if (t === "transcript") {
            if (m.text) this.hooks.onTranscript?.(m.text, m);
          } else if (t === "reply") {
            this.hooks.onReply?.(m);
          } else if (t === "tts_done") {
            this.hooks.onTtsDone?.(m);
          } else if (t === "error") {
            this.hooks.onError?.(m.error || "voice error", m);
          }
        };

        this.ws.send(
          JSON.stringify({
            type: "hello",
            conversation_id: this.hooks.getConversationId?.() ?? null,
            mode: wsModeFromChatMode(this.hooks.getChatMode?.() || "agent"),
            tts: this.hooks.getTts?.() ?? true,
          }),
        );

        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
          },
        });
        this.ctx = new AudioContext({ sampleRate: 16000 });
        const srcNode = this.ctx.createMediaStreamSource(this.stream);
        const meterEl = this.hooks.meterEl;
        const handleChunk = (f32) => {
          let peak = 0;
          const i16 = new Int16Array(f32.length);
          for (let i = 0; i < f32.length; i++) {
            const s = Math.max(-1, Math.min(1, f32[i]));
            i16[i] = s * 32767;
            peak = Math.max(peak, Math.abs(s));
          }
          if (meterEl) meterEl.style.width = `${Math.min(100, peak * 140)}%`;
          if (this.ws && this.ws.readyState === 1) this.ws.send(i16.buffer);
        };

        if (this.ctx.audioWorklet && window.AudioWorkletNode) {
          await this.ctx.audioWorklet.addModule("/static/pcm-capture-worklet.js?v=20261116ui");
          this.node = new AudioWorkletNode(this.ctx, "pcm-capture", {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            outputChannelCount: [1],
          });
          this.node.port.onmessage = (e) => handleChunk(e.data);
        } else {
          this.node = this.ctx.createScriptProcessor(1024, 1, 1);
          this.node.onaudioprocess = (e) => handleChunk(e.inputBuffer.getChannelData(0));
        }
        this.mute = this.ctx.createGain();
        this.mute.gain.value = 0;
        srcNode.connect(this.node);
        this.node.connect(this.mute);
        this.mute.connect(this.ctx.destination);
        this.hooks.onStarted?.();
      } catch (err) {
        this.stop("error");
        throw err;
      } finally {
        this._starting = false;
      }
    }

    _teardownAudio() {
      this.stopPlayback();
      try {
        if (this.node?.port) this.node.port.onmessage = null;
        this.node?.disconnect();
      } catch (_) {
        /* ignore */
      }
      try {
        this.mute?.disconnect();
      } catch (_) {
        /* ignore */
      }
      this.stream?.getTracks().forEach((t) => t.stop());
      this.ctx?.close().catch(() => {});
      this.ws = null;
      this.ctx = null;
      this.stream = null;
      this.node = null;
      this.mute = null;
    }

    stop(_reason) {
      this._active = false;
      try {
        if (this.ws && this.ws.readyState === 1) this.ws.close();
      } catch (_) {
        /* ignore */
      }
      this._teardownAudio();
      this.hooks.onStop?.(_reason);
    }
  }

  window.SahirVoiceClient = SahirVoiceClient;
  window.sahirWsModeFromChatMode = wsModeFromChatMode;
})();