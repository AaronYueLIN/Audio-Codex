# Audio Codex R10 — UI Detail Polish

Scope: detail-only refinement. Existing font stack, product layout, navigation structure,
Toyota 2P5 palette, artwork treatment, and core UI design are intentionally retained.

Refined details:
- complete focus-visible, disabled, pressed, input-hover, selection and checkbox states;
- subtle surface/hairline consistency without changing component geometry;
- search field focus-within feedback;
- small modal and toast entrance/exit motion plus reduced-motion support;
- sticky top bar gains only a subtle hairline when content is actually scrolled;
- Toast gets proper live-region semantics;
- active navigation gets aria-current and sidebar control gets state-aware labels;
- search results are protected from stale async responses during rapid typing;
- AI submit is guarded against accidental duplicate concurrent sends;
- compact (<950px) sidebar reuses the existing compact microphone sizing instead of clipping;
- compact Jobs navigation now uses the same existing Jobs glyph rule as manual collapse.

Validation:
- original font-family declaration is byte-for-byte unchanged;
- core desktop geometry signatures are unchanged;
- only AudioCodex/backend/ui/index.html is changed among original archive members;
- all other original files and binaries are copied byte-for-byte.
