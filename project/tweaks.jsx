/* ============================================================
   Tweaks panel — look + behavior + run configuration
   ============================================================ */

function DendraiTweaks({ tweaks, setTweak, hitl, setHitl, velocity, setVelocity }) {
  return (
    <TweaksPanel title="Tweaks">
      <TweakSection label="Look">
        <TweakRadio
          label="Accent"
          value={tweaks.accent}
          options={[
            { label: "Emerald", value: "emerald" },
            { label: "Indigo",  value: "indigo" },
            { label: "Slate",   value: "slate" },
            { label: "Dendrai Forest Green", value: "forest" },
          ]}
          onChange={(v) => setTweak("accent", v)}
        />
        <TweakRadio
          label="Density"
          value={tweaks.density}
          options={[
            { label: "Comfortable", value: "comfortable" },
            { label: "Compact",     value: "compact" },
          ]}
          onChange={(v) => setTweak("density", v)}
        />
        <TweakRadio
          label="Color scheme"
          value={tweaks.colorScheme || (tweaks.dark ? "dark" : "light")}
          options={[
            { label: "Light",  value: "light" },
            { label: "Dark",   value: "dark" },
            { label: "System", value: "system" },
          ]}
          onChange={(v) => setTweak("colorScheme", v)}
        />
      </TweakSection>

      <TweakSection label="Behavior">
        <TweakSlider
          label="Run animation speed"
          value={tweaks.runSpeed}
          min={0.4} max={2.5} step={0.1}
          onChange={(v) => setTweak("runSpeed", v)}
          unit="x"
        />
        <TweakToggle
          label="Auto-expand stages while running"
          value={tweaks.autoExpand}
          onChange={(v) => setTweak("autoExpand", v)}
        />
      </TweakSection>

      <TweakSection label="Run configuration">
        <TweakToggle
          label="Human review · Risk assessment gate"
          value={!!hitl.risk}
          onChange={(v) => setHitl({ ...hitl, risk: v })}
        />
        <TweakToggle
          label="Human review · Audit scope gate"
          value={!!hitl.scope}
          onChange={(v) => setHitl({ ...hitl, scope: v })}
        />
        <TweakToggle
          label="Human review · Action plan generation gate"
          value={!!hitl.map}
          onChange={(v) => setHitl({ ...hitl, map: v })}
        />
        <TweakSlider
          label="Velocity escalation threshold"
          value={velocity}
          min={1} max={5} step={0.5}
          onChange={(v) => setVelocity(v)}
        />
      </TweakSection>

      <TweakSection label="View">
        <TweakSelect
          label="Persona lens"
          value={tweaks.persona}
          options={[
            { label: "Internal Audit",        value: "Internal Audit" },
            { label: "Board / Audit Comm.",   value: "Board / Audit Committee" },
            { label: "CFO / Treasury",        value: "CFO / Treasury" },
            { label: "CRO / ERM",             value: "CRO / ERM" },
          ]}
          onChange={(v) => setTweak("persona", v)}
        />
      </TweakSection>
    </TweaksPanel>
  );
}

window.DendraiTweaks = DendraiTweaks;
