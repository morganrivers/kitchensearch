class ZoomManager:
    MIN, MAX, STEP_KEY, STEP_WHEEL = 0.6, 2.2, 1.1, 1.05

    def __init__(self, initial=1.0, on_change=None):
        assert isinstance(initial, (int, float)), f"initial must be numeric, got {type(initial)}"
        self._f = self._clamp(float(initial))
        self._on_change = on_change

    @property
    def factor(self):
        return self._f

    def scaled(self, base_px_or_pt):
        assert isinstance(base_px_or_pt, (int, float)), (
            f"scaled expects numeric, got {type(base_px_or_pt)}")
        return max(1, int(round(base_px_or_pt * self._f)))

    def zoom_in(self):
        self._set(self._f * self.STEP_KEY)

    def zoom_out(self):
        self._set(self._f / self.STEP_KEY)

    def wheel(self, notches):
        self._set(self._f * (self.STEP_WHEEL ** notches))

    def reset(self):
        self._set(1.0)

    def _set(self, v):
        v = round(self._clamp(v), 2)
        if v == self._f:
            return
        self._f = v
        if self._on_change:
            self._on_change(v)

    @classmethod
    def _clamp(cls, v):
        return max(cls.MIN, min(cls.MAX, v))
