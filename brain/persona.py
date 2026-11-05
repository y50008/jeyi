"""persona.py — 心情系统 + 证据制人格层（§16.4 补丁 P）

心情：24h 半衰回落，负面比正面留得久，道歉降火 30%（dsh-identity 实测参数）。
traits：模型只能提议并附证据；≥3 次独立证据才 durable；纯情绪只能 tentative，
90 天无确认自动退休；Owner 可否决不能强写；durable 超上限最弱的降级；
durable traits 反向调节主动倾向 ±0.12。
"""
from __future__ import annotations
from . import clock

EMOTIONS = {  # (valence, arousal)
    "喜悦": (0.8, 0.7), "信任": (0.6, 0.3), "期待": (0.5, 0.6),
    "惊讶": (0.1, 0.8), "难过": (-0.7, 0.4), "害怕": (-0.8, 0.7), "生气": (-0.9, 0.8),
}
MOOD_HALF_LIFE = 24.0 * 3600
APOLOGY_DROP = 0.3
DURABLE_EVIDENCE = 3
TENTATIVE_TTL = 90 * 86400
DURABLE_CAP = 20
BEHAVIOR_BIAS = 0.12

class Mood:
    def __init__(self): self.state = None; self.intensity = 0.0; self.at = clock.now()
    def set(self, emotion: str, intensity: float = 0.7):
        assert emotion in EMOTIONS; self.state, self.intensity, self.at = emotion, intensity, clock.now()
    def value(self, t=None) -> float:
        """当前心情值 ∈ [-1,1]：24h 半衰；负面留得久（半衰 ×1.5）。"""
        if not self.state: return 0.0
        t = t or clock.now()
        v, _ = EMOTIONS[self.state]
        hl = MOOD_HALF_LIFE * (1.5 if v < 0 else 1.0)
        return v * self.intensity * 0.5 ** ((t - self.at) / hl)
    def apologize(self): self.intensity *= (1 - APOLOGY_DROP)

class Trait:
    def __init__(self, name, ref, emotional, by):
        self.name, self.proposed_by = name, by
        self.evidence = [ref] if ref else []
        self.emotional_only = bool(emotional)
        self.state = "tentative"      # tentative/durable/vetoed
        self.proposed_at = clock.now()
    def independent_count(self): return len(set(self.evidence))

class Persona:
    def __init__(self):
        self.traits: dict[str, Trait] = {}
        self.mood = Mood()
    def propose(self, name, ref="", emotional=False, by="agent"):
        """模型只能提议，附证据（§16.4）。"""
        self.traits.setdefault(name, Trait(name, ref, emotional, by)); return self.traits[name]
    def add_evidence(self, name, ref, emotional=False):
        tr = self.traits[name]; tr.evidence.append(ref); tr.emotional_only &= emotional
        if tr.state == "tentative" and tr.independent_count() >= DURABLE_EVIDENCE \
                and not tr.emotional_only:
            tr.state = "durable"
            self._enforce_cap()
        return tr
    def owner_veto(self, name): self.traits[name].state = "vetoed"
    def _enforce_cap(self):
        ds = sorted((t for t in self.traits.values() if t.state == "durable"),
                    key=lambda t: t.independent_count())
        while len(ds) > DURABLE_CAP:
            ds[0].state = "tentative"; ds.pop(0)
    def sweep(self, t=None):
        """tentative 90 天无确认自动退休。"""
        t = t or clock.now()
        for tr in self.traits.values():
            if tr.state == "tentative" and t - tr.proposed_at > TENTATIVE_TTL:
                tr.state = "retired"
    def bias(self) -> float:
        """durable traits 调节主动倾向，合计上限 ±0.12（v3 会随条数线性放大到 2.4）。"""
        raw = sum(BEHAVIOR_BIAS if t.state == "durable" else 0.0
                  for t in self.traits.values())
        return max(-BEHAVIOR_BIAS, min(BEHAVIOR_BIAS, raw))
