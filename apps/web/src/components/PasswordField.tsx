import { useState } from "react";

interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  error?: string | null;
}

export default function PasswordField({ id, label, value, onChange, autoComplete, error }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="auth-field-group">
      <div className="auth-label-row"><label htmlFor={id}>{label}</label></div>
      <div className={error ? "auth-field invalid" : "auth-field"}>
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          required
        />
        <button type="button" onClick={() => setVisible((current) => !current)} aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`} aria-pressed={visible}>
          {visible ? "Hide" : "Show"}
        </button>
      </div>
      {error && <p className="auth-field-error" id={`${id}-error`}>{error}</p>}
    </div>
  );
}
