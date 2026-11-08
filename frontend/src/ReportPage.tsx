import { useState, type FormEvent } from 'react';
import { createWorkOrder } from './api';
import {
  MANAGED_PROPERTIES,
  PROBLEM_OPTIONS,
  URGENCY_OPTIONS,
  type ProblemType,
  type Urgency,
} from './types';
import './ReportPage.css';

type Status = 'form' | 'submitting' | 'done' | 'error';

const DEFAULT_ADDRESS = MANAGED_PROPERTIES[0].address;

export default function ReportPage() {
  const [problem, setProblem] = useState<ProblemType>('no_hot_water');
  const [problemOther, setProblemOther] = useState('');
  const [description, setDescription] = useState('');
  const [address, setAddress] = useState(DEFAULT_ADDRESS);
  const [unit, setUnit] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [urgency, setUrgency] = useState<Urgency>('soon');
  const [status, setStatus] = useState<Status>('form');
  const [error, setError] = useState('');
  const [submittedTitle, setSubmittedTitle] = useState('');

  const selectedProperty =
    MANAGED_PROPERTIES.find((property) => property.address === address) ??
    MANAGED_PROPERTIES[0];
  const showUnit = selectedProperty.kind === 'apartment';

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setStatus('submitting');
    setError('');

    try {
      const order = await createWorkOrder({
        problem,
        problem_other: problem === 'other' ? problemOther : undefined,
        description,
        address,
        unit: showUnit && unit ? unit : undefined,
        name,
        email,
        urgency,
      });
      setSubmittedTitle(order.title);
      setStatus('done');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send request');
      setStatus('error');
    }
  };

  const resetForm = () => {
    setProblem('no_hot_water');
    setProblemOther('');
    setDescription('');
    setAddress(DEFAULT_ADDRESS);
    setUnit('');
    setName('');
    setEmail('');
    setUrgency('soon');
    setStatus('form');
  };

  if (status === 'done') {
    return (
      <div className="report">
        <div className="report-card report-success">
          <p className="report-eyebrow">Request sent</p>
          <h1>We’ve got it.</h1>
          <p className="report-copy">
            Your note about <strong>{submittedTitle}</strong> is with the
            property team. Someone will follow up soon.
          </p>
          <button className="report-secondary" type="button" onClick={resetForm}>
            Submit another
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="report">
      <form className="report-card" onSubmit={onSubmit}>
        <p className="report-eyebrow">Maintenance request</p>
        <h1>Need help at the property?</h1>
        <p className="report-copy">
          Tell us what’s going on. This goes straight to the property manager.
        </p>

        <label className="field">
          <span>What’s wrong?</span>
          <select
            value={problem}
            onChange={(event) => setProblem(event.target.value as ProblemType)}
            required
          >
            {PROBLEM_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        {problem === 'other' && (
          <label className="field">
            <span>What is it?</span>
            <input
              value={problemOther}
              onChange={(event) => setProblemOther(event.target.value)}
              placeholder="Short title"
              maxLength={80}
              required
            />
          </label>
        )}

        <label className="field">
          <span>Describe the problem</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Example: No hot water since this morning. Cold only in the shower and kitchen sink."
            rows={4}
            required
          />
        </label>

        <label className="field">
          <span>Property</span>
          <select
            value={address}
            onChange={(event) => {
              setAddress(event.target.value);
              setUnit('');
            }}
            required
          >
            {MANAGED_PROPERTIES.map((property) => (
              <option key={property.address} value={property.address}>
                {property.label}
              </option>
            ))}
          </select>
        </label>

        <div className="field-row">
          {showUnit && (
            <label className="field">
              <span>Unit (optional)</span>
              <input
                value={unit}
                onChange={(event) => setUnit(event.target.value)}
                placeholder="4B"
              />
            </label>
          )}
          <label className="field">
            <span>Your name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Alex"
              required
            />
          </label>
        </div>

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@email.com"
            required
          />
        </label>

        <fieldset className="urgency">
          <legend>How urgent is this?</legend>
          <div className="urgency-options">
            {URGENCY_OPTIONS.map((option) => (
              <label
                key={option.value}
                className={
                  urgency === option.value
                    ? 'urgency-option selected'
                    : 'urgency-option'
                }
              >
                <input
                  type="radio"
                  name="urgency"
                  value={option.value}
                  checked={urgency === option.value}
                  onChange={() => setUrgency(option.value)}
                />
                <span className="urgency-label">{option.label}</span>
                <span className="urgency-hint">{option.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>

        {(status === 'error' || error) && (
          <p className="report-error" role="alert">
            {error || 'Something went wrong. Try again.'}
          </p>
        )}

        <button
          className="report-submit"
          type="submit"
          disabled={status === 'submitting'}
        >
          {status === 'submitting' ? 'Sending…' : 'Send request'}
        </button>
      </form>
    </div>
  );
}
