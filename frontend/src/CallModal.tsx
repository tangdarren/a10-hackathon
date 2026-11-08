import { Conversation, stripAudioTags } from '@elevenlabs/client';
import { useEffect, useRef, useState } from 'react';
import { callerSession, completeCall } from './api';

type Line = { speaker: 'caller' | 'fixer'; text: string };
type Phase = 'starting' | 'live' | 'ending' | 'error';

type CallModalProps = {
  orderId: string;
  index: number;
  contractorName: string;
  onClose: () => void;
};

const WRAP_UP_RE =
  /\b(goodbye|good bye|bye|booking reference|reference is|tenant will let|talk soon|take care|have a good)\b/i;

function cleanLine(text: string): string {
  return stripAudioTags(text || '')
    .replace(/\[[^\[\]]{0,40}\]/g, '')
    .replace(/!+/g, '.')
    .replace(/！/g, '.')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/**
 * Live voice call with the ElevenLabs Dispatch agent. You play the contractor
 * on the mic; Dispatch confirms the Outreach quote, then we book it on End.
 */
export default function CallModal({
  orderId,
  index,
  contractorName,
  onClose,
}: CallModalProps) {
  const [phase, setPhase] = useState<Phase>('starting');
  const [speaking, setSpeaking] = useState(false);
  const [wrappingUp, setWrappingUp] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState('');
  const conversationRef = useRef<Conversation | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const reportedRef = useRef(false);
  const wrapUpRef = useRef(false);
  const hangupTimerRef = useRef<number | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  const clearHangupTimer = () => {
    if (hangupTimerRef.current !== null) {
      window.clearTimeout(hangupTimerRef.current);
      hangupTimerRef.current = null;
    }
  };

  // Tell the backend the call is over (once), then close — books Outreach quote.
  const report = () => {
    if (reportedRef.current) return;
    reportedRef.current = true;
    clearHangupTimer();
    const id = conversationIdRef.current;
    if (!id) {
      onClose();
      return;
    }
    setPhase('ending');
    void completeCall(orderId, index, id)
      .catch(() => undefined)
      .finally(onClose);
  };

  const scheduleHangup = () => {
    if (reportedRef.current || hangupTimerRef.current !== null) return;
    hangupTimerRef.current = window.setTimeout(() => {
      hangupTimerRef.current = null;
      const conversation = conversationRef.current;
      if (conversation) {
        void conversation.endSession();
      } else {
        report();
      }
    }, 5000);
  };

  useEffect(() => {
    let cancelled = false;

    const start = async () => {
      try {
        const session = await callerSession(orderId, index);
        if (cancelled) return;
        if (session.conversation_id) {
          conversationIdRef.current = session.conversation_id;
        }

        await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) return;

        const connection =
          session.connection_type === 'webrtc' && session.conversation_token
            ? {
                conversationToken: session.conversation_token,
                connectionType: 'webrtc' as const,
              }
            : {
                signedUrl: session.signed_url!,
                connectionType: 'websocket' as const,
              };

        const conversation = await Conversation.startSession({
          ...connection,
          dynamicVariables: session.dynamic_variables,
          stripAudioTags: true,
          onConnect: ({ conversationId }) => {
            conversationIdRef.current = conversationId;
            setPhase('live');
          },
          onMessage: ({ role, message }) => {
            const text = cleanLine(message);
            if (!text) return;
            setLines((current) => [
              ...current,
              { speaker: role === 'agent' ? 'caller' : 'fixer', text },
            ]);
            if (role === 'agent' && WRAP_UP_RE.test(text)) {
              wrapUpRef.current = true;
              setWrappingUp(true);
            }
          },
          onModeChange: ({ mode }) => {
            const isSpeaking = mode === 'speaking';
            setSpeaking(isSpeaking);
            // After Dispatch finishes the wrap-up line, hang up in 5 seconds.
            if (!isSpeaking && wrapUpRef.current) {
              scheduleHangup();
            }
          },
          onDisconnect: () => report(),
          onError: (message) => {
            setError(typeof message === 'string' ? message : 'Call failed');
            setPhase('error');
          },
        });
        if (cancelled) {
          void conversation.endSession();
          return;
        }
        conversationRef.current = conversation;
        const id = conversation.getId();
        if (id) conversationIdRef.current = id;
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : 'Could not start the call',
        );
        setPhase('error');
      }
    };

    void start();
    return () => {
      cancelled = true;
      clearHangupTimer();
      void conversationRef.current?.endSession();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId, index]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight });
  }, [lines]);

  const hangUp = () => {
    clearHangupTimer();
    const conversation = conversationRef.current;
    if (conversation) {
      void conversation.endSession(); // onDisconnect reports + books the quote
    } else {
      report();
    }
  };

  const statusText =
    phase === 'starting'
      ? 'Connecting to Dispatch… allow microphone access'
      : phase === 'ending'
        ? 'Call ended — saving booking from Outreach quote…'
        : phase === 'error'
          ? error || 'Call failed'
          : speaking
            ? 'Dispatch is speaking'
            : wrappingUp
              ? 'Wrapping up — ending in a few seconds'
              : `Your turn — answer as ${contractorName}`;

  return (
    <div className="qr-overlay" role="dialog" aria-modal="true" aria-label="Live call">
      <div className="results-modal call-modal">
        <div className="results-modal-header">
          <div>
            <p className="results-eyebrow">Caller · ElevenLabs live voice</p>
            <h2>Calling {contractorName}</h2>
            <p className={`call-status call-status-${phase}${speaking ? ' speaking' : ''}`}>
              <span className="call-status-dot" aria-hidden="true" />
              {statusText}
            </p>
            <p className="call-role-hint">
              You are the plumber. Confirm the Outreach quote out loud. Ending
              the call saves that booking so you can Follow up.
            </p>
          </div>
          {phase === 'error' ? (
            <button className="reset-btn" type="button" onClick={onClose}>
              Close
            </button>
          ) : (
            <button
              className="hangup-btn"
              type="button"
              onClick={hangUp}
              disabled={phase === 'ending'}
            >
              End call
            </button>
          )}
        </div>

        <div className="transcript call-transcript" ref={transcriptRef}>
          {lines.length === 0 ? (
            <p className="results-footnote">
              Waiting for Dispatch to open… talk into your mic when it asks a
              question.
            </p>
          ) : (
            lines.map((line, i) => (
              <div className={`transcript-line ${line.speaker}`} key={i}>
                <span className="transcript-speaker">
                  {line.speaker === 'caller' ? 'Dispatch' : contractorName}
                </span>
                <p>{line.text}</p>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
