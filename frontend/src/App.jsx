import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Cpu,
  Crosshair,
  Database,
  Fingerprint,
  Globe2,
  LockKeyhole,
  MapPin,
  Menu,
  Mic,
  MicOff,
  Orbit,
  Radio,
  RefreshCw,
  ScanFace,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Terminal,
  UserRound,
  Volume2,
  WalletCards,
  X,
  XCircle,
  Zap,
} from "lucide-react";

import "./index.css";

const API = "http://127.0.0.1:8000";

/* =========================================================
   SAFE HELPERS
========================================================= */

function safeText(value, fallback = "") {
  if (value === null || value === undefined) {
    return fallback;
  }

  if (typeof value === "string") {
    return value;
  }

  if (
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => safeText(item))
      .filter(Boolean)
      .join(", ");
  }

  if (typeof value === "object") {
    return (
      value.message ??
      value.detail ??
      value.error ??
      value.text ??
      value.label ??
      value.value ??
      JSON.stringify(value)
    );
  }

  return fallback;
}

function money(value) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return "₹0";
  }

  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(number);
}

function numberValue(value, fallback = 0) {
  const number = Number(value);

  return Number.isFinite(number)
    ? number
    : fallback;
}

function clamp(value, min, max) {
  return Math.min(
    max,
    Math.max(min, value)
  );
}

function formatDate(value) {
  if (!value) {
    return "—";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return safeText(value);
  }

  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function nowLocalInput() {
  const date = new Date();

  const pad = (value) =>
    String(value).padStart(2, "0");

  return `${date.getFullYear()}-${pad(
    date.getMonth() + 1
  )}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

function riskTone(score) {
  const value = numberValue(score);

  if (value <= 30) {
    return "low";
  }

  if (value <= 75) {
    return "alert";
  }

  return "high";
}

function riskLabel(score) {
  const value = numberValue(score);

  if (value <= 30) {
    return "LOW RISK";
  }

  if (value <= 75) {
    return "ALERT";
  }

  return "HIGH RISK";
}

function decisionIcon(decision) {
  const value =
    safeText(decision).toUpperCase();

  if (
    value.includes("BLOCK") ||
    value.includes("HOLD")
  ) {
    return <XCircle size={17} />;
  }

  if (
    value.includes("VERIFY") ||
    value.includes("ALERT") ||
    value.includes("MONITOR")
  ) {
    return <ShieldAlert size={17} />;
  }

  return <ShieldCheck size={17} />;
}

/* =========================================================
   API
========================================================= */

async function apiRequest(
  endpoint,
  options = {}
) {
  const response = await fetch(
    `${API}${endpoint}`,
    {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    }
  );

  const contentType =
    response.headers.get(
      "content-type"
    ) || "";

  let data = null;

  if (
    contentType.includes(
      "application/json"
    )
  ) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  } else {
    try {
      const text =
        await response.text();

      data = text || null;
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    let message =
      "Request failed.";

    if (data) {
      if (
        typeof data === "string"
      ) {
        message = data;
      } else if (
        Array.isArray(
          data.detail
        )
      ) {
        message = data.detail
          .map((item) => {
            if (
              typeof item ===
              "string"
            ) {
              return item;
            }

            return (
              item.msg ||
              item.message ||
              JSON.stringify(item)
            );
          })
          .join(" • ");
      } else {
        message =
          data.detail ||
          data.message ||
          data.error ||
          message;
      }
    }

    const error =
      new Error(
        safeText(
          message,
          `HTTP ${response.status}`
        )
      );

    error.status =
      response.status;

    error.data = data;

    throw error;
  }

  return data;
}

/* =========================================================
   QUESTION HELPERS
========================================================= */

function normalizeQuestion(question) {
  if (!question) {
    return null;
  }

  if (typeof question === "string") {
    return {
      text: question,
      id: null,
      options: [],
    };
  }

  if (
    typeof question !==
    "object"
  ) {
    return null;
  }

  const text =
    question.tts_text ??
    question.question_text ??
    question.text ??
    question.question ??
    question.prompt ??
    "";

  const id =
    question.question_id ??
    question.id ??
    question._id ??
    null;

  const rawOptions =
    question.options ??
    question.answer_options ??
    question.choices ??
    question.answers ??
    [];

  const options =
    Array.isArray(rawOptions)
      ? rawOptions
          .map((option) => {
            if (
              typeof option ===
              "string"
            ) {
              return option;
            }

            if (
              option &&
              typeof option ===
                "object"
            ) {
              return (
                option.label ??
                option.text ??
                option.value ??
                option.answer ??
                ""
              );
            }

            return "";
          })
          .filter(Boolean)
      : [];

  return {
    ...question,
    id,
    text: safeText(text),
    options,
  };
}

function getQuestion(data) {
  if (!data) {
    return null;
  }

  const candidates = [
    data.next_question,
    data.current_question,
    data.question,
    data.verification?.next_question,
    data.verification?.current_question,
    data.verification?.question,
  ];

  for (const candidate of candidates) {
    const normalized =
      normalizeQuestion(
        candidate
      );

    if (
      normalized &&
      normalized.text
    ) {
      return normalized;
    }
  }

  return null;
}

function getSessionId(data) {
  if (!data) {
    return null;
  }

  return (
    data.verification_session_id ??
    data.verification?.verification_session_id ??
    data.session_id ??
    data.verification?.session_id ??
    null
  );
}

/* =========================================================
   INITIAL FORM
========================================================= */

const initialForm = {
  amount: "2500",

  transaction_category:
    "Shopping",

  transaction_purpose:
    "Shopping",

  recipient_upi_id:
    "demo-recipient@upi",

  device:
    "UNKNOWN-DEVICE",

  ip_address:
    "127.0.0.1",

  ip_location:
    "Kolkata, West Bengal",

  location_name:
    "Kolkata, West Bengal",

  latitude:
    "22.5726",

  longitude:
    "88.3639",

  transaction_time:
    nowLocalInput(),
};

/* =========================================================
   APP
========================================================= */

export default function App() {
  const [users, setUsers] =
    useState([]);

  const [selectedUserId, setSelectedUserId] =
    useState("");

  const [transactions, setTransactions] =
    useState([]);

  const [dashboard, setDashboard] =
    useState(null);

  const [form, setForm] =
    useState(initialForm);

  const [result, setResult] =
    useState(null);

  const [loading, setLoading] =
    useState(false);

  const [initialLoading, setInitialLoading] =
    useState(true);

  const [error, setError] =
    useState("");

  const [menuOpen, setMenuOpen] =
    useState(false);

  const [verificationOpen, setVerificationOpen] =
    useState(false);

  const [verificationLoading, setVerificationLoading] =
    useState(false);

  const [verificationSessionId, setVerificationSessionId] =
    useState("");

  const [currentQuestion, setCurrentQuestion] =
    useState(null);

  const [answer, setAnswer] =
    useState("");

  const [listening, setListening] =
    useState(false);

  const recognitionRef =
    useRef(null);

  /* =====================================================
     SPEECH
  ===================================================== */

  const speechAvailable =
    typeof window !==
      "undefined" &&
    "speechSynthesis" in window;

  const recognitionAvailable =
    typeof window !==
      "undefined" &&
    !!(
      window.SpeechRecognition ||
      window.webkitSpeechRecognition
    );

  function speakQuestion(question) {
    if (
      !speechAvailable
    ) {
      return;
    }

    const normalized =
      normalizeQuestion(
        question
      );

    if (
      !normalized?.text
    ) {
      return;
    }

    window.speechSynthesis.cancel();

    const utterance =
      new SpeechSynthesisUtterance(
        normalized.text
      );

    utterance.lang = "en-IN";
    utterance.rate = 0.95;
    utterance.pitch = 1;

    window.speechSynthesis.speak(
      utterance
    );
  }

  function startVoiceInput() {
    if (
      !recognitionAvailable
    ) {
      setError(
        "Voice input is not supported by this browser. You can type the answer instead."
      );
      return;
    }

    const SpeechRecognition =
      window.SpeechRecognition ||
      window.webkitSpeechRecognition;

    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }

    const recognition =
      new SpeechRecognition();

    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onstart = () => {
      setListening(true);
      setError("");
    };

    recognition.onresult = (
      event
    ) => {
      const transcript =
        event.results?.[0]?.[0]
          ?.transcript || "";

      setAnswer(
        safeText(transcript)
      );
    };

    recognition.onerror = (
      event
    ) => {
      console.error(
        "Speech recognition error:",
        event
      );

      setError(
        "Voice input could not be understood. Please try again or type the answer."
      );

      setListening(false);
    };

    recognition.onend = () => {
      setListening(false);
    };

    recognitionRef.current =
      recognition;

    recognition.start();
  }

  /* =====================================================
     LOAD DATA
  ===================================================== */

  async function loadUsers() {
    const data =
      await apiRequest(
        "/users"
      );

    const list = Array.isArray(
      data
    )
      ? data
      : Array.isArray(
          data?.users
        )
      ? data.users
      : [];

    setUsers(list);

    if (list.length > 0) {
      const first =
        list[0];

      const id = safeText(
        first.user_id ??
          first.id ??
          first._id
      );

      setSelectedUserId(
        id
      );
    }
  }

  async function loadTransactions(
    userId
  ) {
    if (!userId) {
      setTransactions([]);
      return;
    }

    try {
      const data =
        await apiRequest(
          `/transactions/${encodeURIComponent(
            userId
          )}`
        );

      const list =
        Array.isArray(data)
          ? data
          : Array.isArray(
              data?.transactions
            )
          ? data.transactions
          : [];

      setTransactions(
        list
      );
    } catch (err) {
      console.warn(
        "Transaction history could not be loaded:",
        err
      );

      setTransactions([]);
    }
  }

  async function loadDashboard() {
    try {
      const data =
        await apiRequest(
          "/soc/dashboard"
        );

      setDashboard(
        data
      );
    } catch (err) {
      console.warn(
        "Dashboard unavailable:",
        err
      );
    }
  }

  async function initialize() {
    setInitialLoading(true);

    try {
      await Promise.all([
        loadUsers(),
        loadDashboard(),
      ]);
    } catch (err) {
      console.error(
        "Initialization failed:",
        err
      );

      setError(
        getErrorMessage(err)
      );
    } finally {
      setInitialLoading(false);
    }
  }

  useEffect(() => {
    initialize();

    return () => {
      try {
        recognitionRef.current?.stop();
      } catch {
        // ignore
      }

      if (
        typeof window !==
          "undefined" &&
        "speechSynthesis" in
          window
      ) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  useEffect(() => {
    if (selectedUserId) {
      loadTransactions(
        selectedUserId
      );
    }
  }, [selectedUserId]);

  /* =====================================================
     FORM
  ===================================================== */

  function updateForm(
    field,
    value
  ) {
    setForm(
      (previous) => ({
        ...previous,
        [field]: value,
      })
    );
  }

  function selectedUser() {
    return users.find(
      (user) =>
        safeText(
          user.user_id ??
            user.id ??
            user._id
        ) ===
        selectedUserId
    );
  }

  /* =====================================================
     BENEFICIARY INTERNAL ID
     
     User does NOT see this.
     Backend schema requires beneficiary_id.
  ===================================================== */

  function buildInternalBeneficiaryId() {
    const upi =
      form.recipient_upi_id.trim();

    if (!upi) {
      return "AUTO-BENEFICIARY";
    }

    return `UPI-${upi
      .replace(/[^a-zA-Z0-9]/g, "-")
      .slice(0, 48)}`;
  }

  /* =====================================================
     ANALYZE PAYMENT
  ===================================================== */

  async function analyzePayment(
    event
  ) {
    event?.preventDefault();

    if (!selectedUserId) {
      setError(
        "Please select a user before analyzing the payment."
      );
      return;
    }

    const amount =
      Number(form.amount);

    if (
      !Number.isFinite(amount) ||
      amount <= 0
    ) {
      setError(
        "Please enter a valid transaction amount."
      );
      return;
    }

    if (
      !form.recipient_upi_id.trim()
    ) {
      setError(
        "Please enter the recipient UPI ID."
      );
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);
    setVerificationOpen(false);
    setCurrentQuestion(null);
    setVerificationSessionId("");
    setAnswer("");

    try {
      /*
       * IMPORTANT:
       * These names exactly match the
       * backend Swagger schema shown
       * in your screenshot.
       */
      const payload = {
        user_id:
          selectedUserId,

        amount,

        transaction_category:
          form.transaction_category,

        transaction_purpose:
          form.transaction_purpose,

        recipient_upi_id:
          form.recipient_upi_id.trim(),

        /*
         * Hidden/internal backend field.
         * It is NOT shown in the UI.
         */
        beneficiary_id:
          buildInternalBeneficiaryId(),

        device:
          form.device.trim() ||
          "UNKNOWN-DEVICE",

        ip_address:
          form.ip_address.trim() ||
          "127.0.0.1",

        ip_location:
          form.ip_location.trim() ||
          form.location_name.trim() ||
          "Kolkata, West Bengal",

        location_name:
          form.location_name.trim() ||
          "Kolkata, West Bengal",

        latitude:
          Number(form.latitude) ||
          0,

        longitude:
          Number(form.longitude) ||
          0,

        transaction_time:
          form.transaction_time ||
          nowLocalInput(),

        face_verified: true,

        identity_verified: true,
      };

      console.log(
        "SecureFlow-AI analyze payload:",
        payload
      );

      const data =
        await apiRequest(
          "/analyze-payment",
          {
            method: "POST",
            body: JSON.stringify(
              payload
            ),
          }
        );

      console.log(
        "SecureFlow-AI analyze response:",
        data
      );

      setResult(
        data || {}
      );

      const sessionId =
        getSessionId(data);

      const question =
        getQuestion(data);

      if (sessionId) {
        setVerificationSessionId(
          safeText(sessionId)
        );
      }

      if (
        question
      ) {
        setCurrentQuestion(
          question
        );

        setVerificationOpen(
          true
        );

        setTimeout(() => {
          speakQuestion(
            question
          );
        }, 400);
      }

      await Promise.all([
        loadDashboard(),
        loadTransactions(
          selectedUserId
        ),
      ]);
    } catch (err) {
      console.error(
        "Analyze payment failed:",
        {
          status: err?.status,
          message: err?.message,
          data: err?.data,
        }
      );

      setError(
        getErrorMessage(err)
      );
    } finally {
      setLoading(false);
    }
  }

  /* =====================================================
     VERIFICATION
  ===================================================== */

  async function submitVerification() {
    if (
      !verificationSessionId
    ) {
      setError(
        "Verification session is missing. Please analyze the payment again."
      );
      return;
    }

    if (!answer.trim()) {
      setError(
        "Please select or provide an answer."
      );
      return;
    }

    setVerificationLoading(
      true
    );

    setError("");

    try {
      /*
       * IMPORTANT:
       * Backend expects verification_session_id.
       */
      const payload = {
        verification_session_id:
          verificationSessionId,

        answer:
          answer.trim(),
      };

      const questionId =
        currentQuestion?.id;

      if (questionId !== null &&
          questionId !== undefined &&
          safeText(questionId)
      ) {
        payload.question_id =
          safeText(questionId);
      }

      console.log(
        "SecureFlow-AI verification payload:",
        payload
      );

      const data =
        await apiRequest(
          "/verify-challenge",
          {
            method: "POST",
            body: JSON.stringify(
              payload
            ),
          }
        );

      console.log(
        "SecureFlow-AI verification response:",
        data
      );

      setResult(
        (previous) => ({
          ...(previous || {}),
          ...(data || {}),
        })
      );

      /*
       * Backend may provide a new
       * question after an incorrect answer.
       */
      const nextQuestion =
        getQuestion(data);

      if (
        nextQuestion
      ) {
        setCurrentQuestion(
          nextQuestion
        );

        setAnswer("");

        setTimeout(() => {
          speakQuestion(
            nextQuestion
          );
        }, 350);

        return;
      }

      /*
       * No more verification questions.
       */
      setVerificationOpen(
        false
      );

      setCurrentQuestion(
        null
      );

      setAnswer("");

      await Promise.all([
        loadDashboard(),
        loadTransactions(
          selectedUserId
        ),
      ]);
    } catch (err) {
      console.error(
        "Verification failed:",
        {
          status: err?.status,
          message: err?.message,
          data: err?.data,
        }
      );

      setError(
        getErrorMessage(err)
      );
    } finally {
      setVerificationLoading(
        false
      );
    }
  }

  /* =====================================================
     RESET
  ===================================================== */

  function resetTransaction() {
    setForm(
      {
        ...initialForm,
        transaction_time:
          nowLocalInput(),
      }
    );

    setResult(null);

    setError("");

    setVerificationOpen(
      false
    );

    setVerificationSessionId(
      ""
    );

    setCurrentQuestion(
      null
    );

    setAnswer("");

    try {
      recognitionRef.current?.stop();
    } catch {
      // ignore
    }

    setListening(false);
  }

  function closeError() {
    setError("");
  }

  /* =====================================================
     DERIVED DATA
  ===================================================== */

  const score =
    numberValue(
      result?.updated_risk_score ??
        result?.risk_score ??
        result?.initial_risk_score ??
        0
    );

  const risk =
    riskTone(score);

  const level =
    safeText(
      result?.risk_level
    ) ||
    riskLabel(score);

  const decision =
    safeText(
      result?.decision
    ) ||
    "PENDING";

  const breakdown =
    result?.risk_breakdown ??
    result?.breakdown ??
    {};

  const factors =
    useMemo(() => {
      if (
        !breakdown ||
        typeof breakdown !==
          "object"
      ) {
        return [];
      }

      return Object.entries(
        breakdown
      )
        .filter(
          ([, value]) =>
            Number.isFinite(
              Number(value)
            )
        )
        .map(
          ([name, value]) => ({
            name,
            value: clamp(
              Number(value),
              0,
              100
            ),
          })
        )
        .sort(
          (a, b) =>
            b.value - a.value
        )
        .slice(0, 9);
    }, [breakdown]);

  const mlProbability =
    numberValue(
      result?.ml_fraud_probability ??
        result?.fraud_probability ??
        result?.ml_probability ??
        0
    );

  const selected =
    selectedUser();

  const questionOptions =
    currentQuestion?.options ||
    [];

  const totalTransactions =
    numberValue(
      dashboard?.total_transactions ??
        dashboard?.transactions ??
        dashboard?.metrics?.total_transactions ??
        transactions.length
    );

  const incidents =
    numberValue(
      dashboard?.total_incidents ??
        dashboard?.incidents ??
        dashboard?.metrics?.total_incidents ??
        0
    );

  const blocked =
    numberValue(
      dashboard?.blocked_transactions ??
        dashboard?.blocked ??
        dashboard?.metrics?.blocked_transactions ??
        0
    );

  /* =====================================================
     RENDER
  ===================================================== */

  if (initialLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-orbit">
          <Orbit size={38} />
        </div>

        <div className="loading-title">
          SECUREFLOW-AI
        </div>

        <div className="loading-subtitle">
          INITIALIZING SECURITY
          COMMAND SYSTEM
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <SpaceBackground />

      {/* =================================================
          HEADER
      ================================================= */}

      <header className="site-header">
        <div className="brand">
          <div className="brand-mark">
            <Shield size={19} />
          </div>

          <div>
            <div className="brand-name">
              SECUREFLOW
              <span>-AI</span>
            </div>

            <div className="brand-sub">
              PAYMENT FRAUD DEFENCE
            </div>
          </div>
        </div>

        <nav
          className={
            menuOpen
              ? "main-nav open"
              : "main-nav"
          }
        >
          <a href="#terminal">
            TERMINAL
          </a>

          <a href="#risk">
            RISK ENGINE
          </a>

          <a href="#architecture">
            ARCHITECTURE
          </a>

          <a href="#history">
            HISTORY
          </a>
        </nav>

        <div className="header-status">
          <span className="status-dot" />
          SYSTEM ONLINE
        </div>

        <button
          className="menu-button"
          onClick={() =>
            setMenuOpen(
              (value) => !value
            )
          }
          aria-label="Menu"
        >
          {menuOpen ? (
            <X size={20} />
          ) : (
            <Menu size={20} />
          )}
        </button>
      </header>

      {/* =================================================
          HERO
      ================================================= */}

      <main>
        <section className="hero">
          <div className="hero-copy">
            <div className="eyebrow">
              <span />
              REAL-TIME PAYMENT
              SECURITY
            </div>

            <h1>
              DEFEND
              <br />
              EVERY
              <br />
              <em>FLOW.</em>
            </h1>

            <p>
              A layered fraud-defence
              system that observes
              behaviour, context,
              devices, locations and
              transaction history before
              a payment is allowed to
              move.
            </p>

            <a
              href="#terminal"
              className="hero-button"
            >
              OPEN SECURITY
              TERMINAL
              <ArrowRight
                size={16}
              />
            </a>
          </div>

          <div className="hero-visual">
            <div className="hero-core">
              <div className="core-ring ring-one" />
              <div className="core-ring ring-two" />
              <div className="core-ring ring-three" />

              <div className="core-center">
                <ShieldCheck
                  size={42}
                />

                <span>
                  SECURE
                </span>
              </div>

              <div className="orbit-dot dot-one" />
              <div className="orbit-dot dot-two" />
              <div className="orbit-dot dot-three" />
            </div>

            <div className="hero-readout">
              <span>
                ENGINE
              </span>

              <strong>
                HYBRID
              </strong>

              <small>
                RULE + MACHINE
                LEARNING
              </small>
            </div>
          </div>
        </section>

        {/* =================================================
            TERMINAL
        ================================================= */}

        <section
          id="terminal"
          className="section"
        >
          <SectionHeading
            number="01"
            title="TRANSACTION TERMINAL"
            subtitle="Create a new payment event and send it directly to the live SecureFlow-AI risk engine."
          />

          <div className="terminal-grid">
            <div className="panel transaction-panel">
              <PanelHeader
                icon={<WalletCards />}
                title="PAYMENT INPUT"
                status="LIVE"
              />

              <form
                onSubmit={
                  analyzePayment
                }
              >
                <div className="field-grid">
                  <Field
                    label="USER PROFILE"
                    icon={
                      <UserRound
                        size={14}
                      />
                    }
                  >
                    <select
                      value={
                        selectedUserId
                      }
                      onChange={(
                        event
                      ) =>
                        setSelectedUserId(
                          event.target
                            .value
                        )
                      }
                    >
                      {users.length ===
                      0 ? (
                        <option value="">
                          No users available
                        </option>
                      ) : (
                        users.map(
                          (user) => {
                            const id =
                              safeText(
                                user.user_id ??
                                  user.id ??
                                  user._id
                              );

                            const name =
                              safeText(
                                user.name ??
                                  user.full_name ??
                                  user.username ??
                                  id
                              );

                            return (
                              <option
                                key={
                                  id
                                }
                                value={
                                  id
                                }
                              >
                                {name}{" "}
                                —{" "}
                                {id}
                              </option>
                            );
                          }
                        )
                      )}
                    </select>
                  </Field>

                  <Field
                    label="AMOUNT"
                    icon={
                      <CircleDollarSign
                        size={14}
                      />
                    }
                  >
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={
                        form.amount
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "amount",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <Field
                    label="CATEGORY"
                    icon={
                      <Sparkles
                        size={14}
                      />
                    }
                  >
                    <select
                      value={
                        form.transaction_category
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "transaction_category",
                          event.target
                            .value
                        )
                      }
                    >
                      <option>
                        Shopping
                      </option>
                      <option>
                        Food
                      </option>
                      <option>
                        Travel
                      </option>
                      <option>
                        Education
                      </option>
                      <option>
                        Bills
                      </option>
                      <option>
                        Healthcare
                      </option>
                      <option>
                        Other
                      </option>
                    </select>
                  </Field>

                  <Field
                    label="PURPOSE"
                    icon={
                      <Terminal
                        size={14}
                      />
                    }
                  >
                    <select
                      value={
                        form.transaction_purpose
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "transaction_purpose",
                          event.target
                            .value
                        )
                      }
                    >
                      <option>
                        Shopping
                      </option>
                      <option>
                        Food
                      </option>
                      <option>
                        Travel
                      </option>
                      <option>
                        Education
                      </option>
                      <option>
                        Bill Payment
                      </option>
                      <option>
                        Other payment
                      </option>
                    </select>
                  </Field>
                </div>

                <div className="field-grid">
                  <Field
                    label="RECIPIENT UPI"
                    icon={
                      <ArrowRight
                        size={14}
                      />
                    }
                  >
                    <input
                      type="text"
                      value={
                        form.recipient_upi_id
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "recipient_upi_id",
                          event.target
                            .value
                        )
                      }
                      placeholder="recipient@upi"
                    />
                  </Field>

                  <Field
                    label="DEVICE"
                    icon={
                      <Smartphone
                        size={14}
                      />
                    }
                  >
                    <input
                      type="text"
                      value={
                        form.device
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "device",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <Field
                    label="IP ADDRESS"
                    icon={
                      <Globe2
                        size={14}
                      />
                    }
                  >
                    <input
                      type="text"
                      value={
                        form.ip_address
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "ip_address",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <Field
                    label="LOCATION"
                    icon={
                      <MapPin
                        size={14}
                      />
                    }
                  >
                    <input
                      type="text"
                      value={
                        form.location_name
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "location_name",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>
                </div>

                <div className="field-grid">
                  <Field
                    label="LATITUDE"
                    icon={
                      <Crosshair
                        size={14}
                      />
                    }
                  >
                    <input
                      type="number"
                      step="any"
                      value={
                        form.latitude
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "latitude",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <Field
                    label="LONGITUDE"
                    icon={
                      <Crosshair
                        size={14}
                      />
                    }
                  >
                    <input
                      type="number"
                      step="any"
                      value={
                        form.longitude
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "longitude",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <Field
                    label="TRANSACTION TIME"
                    icon={
                      <Clock3
                        size={14}
                      />
                    }
                  >
                    <input
                      type="datetime-local"
                      value={
                        form.transaction_time
                      }
                      onChange={(
                        event
                      ) =>
                        updateForm(
                          "transaction_time",
                          event.target
                            .value
                        )
                      }
                    />
                  </Field>

                  <div className="identity-state">
                    <div className="identity-icon">
                      <Fingerprint
                        size={18}
                      />
                    </div>

                    <div>
                      <span>
                        IDENTITY CHECK
                      </span>

                      <strong>
                        ENABLED
                      </strong>
                    </div>
                  </div>
                </div>

                <div className="form-actions">
                  <button
                    type="button"
                    className="reset-button"
                    onClick={
                      resetTransaction
                    }
                  >
                    RESET
                  </button>

                  <button
                    type="submit"
                    className="analyze-button"
                    disabled={
                      loading ||
                      !selectedUserId
                    }
                  >
                    {loading ? (
                      <>
                        <RefreshCw
                          size={17}
                          className="spin"
                        />
                        ANALYZING
                      </>
                    ) : (
                      <>
                        ANALYZE
                        PAYMENT
                        <ArrowRight
                          size={17}
                        />
                      </>
                    )}
                  </button>
                </div>
              </form>
            </div>

            {/* PROFILE */}
            <div className="side-stack">
              <div className="panel profile-panel">
                <PanelHeader
                  icon={
                    <ScanFace />
                  }
                  title="IDENTITY PROFILE"
                />

                {selected ? (
                  <div className="profile-content">
                    <div className="profile-avatar">
                      <UserRound
                        size={28}
                      />
                    </div>

                    <div>
                      <div className="profile-name">
                        {safeText(
                          selected.name ??
                            selected.full_name ??
                            selected.username ??
                            selectedUserId
                        )}
                      </div>

                      <div className="profile-id">
                        {selectedUserId}
                      </div>
                    </div>

                    <div className="verified-pill">
                      <Check
                        size={13}
                      />
                      VERIFIED
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">
                    No user selected.
                  </div>
                )}
              </div>

              <div className="panel telemetry-panel">
                <PanelHeader
                  icon={
                    <Activity />
                  }
                  title="LIVE TELEMETRY"
                />

                <div className="telemetry-list">
                  <TelemetryRow
                    label="TRANSACTIONS"
                    value={
                      totalTransactions
                    }
                  />

                  <TelemetryRow
                    label="INCIDENTS"
                    value={incidents}
                  />

                  <TelemetryRow
                    label="BLOCKED"
                    value={blocked}
                  />

                  <TelemetryRow
                    label="ML STATUS"
                    value="READY"
                  />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* =================================================
            ERROR
        ================================================= */}

        {error && (
          <section className="error-wrap">
            <div className="error-banner">
              <XCircle
                size={19}
              />

              <div className="error-content">
                <strong>
                  REQUEST FAILED
                </strong>

                <span>
                  {safeText(
                    error,
                    "An unexpected error occurred."
                  )}
                </span>
              </div>

              <button
                onClick={
                  closeError
                }
                aria-label="Close error"
              >
                <X size={17} />
              </button>
            </div>
          </section>
        )}

        {/* =================================================
            RESULT
        ================================================= */}

        {result && (
          <section
            id="risk"
            className="section result-section"
          >
            <SectionHeading
              number="02"
              title="RISK ANALYSIS"
              subtitle="The transaction has passed through the live rule, behavioural and machine-learning analysis pipeline."
            />

            <div className="risk-grid">
              <div className="panel score-panel">
                <PanelHeader
                  icon={
                    <ShieldAlert />
                  }
                  title="CURRENT RISK"
                  status={riskLabel(
                    score
                  )}
                />

                <div className="score-ring-wrap">
                  <div
                    className={`score-ring ${risk}`}
                    style={{
                      "--score":
                        clamp(
                          score,
                          0,
                          100
                        ),
                    }}
                  >
                    <div className="score-inner">
                      <strong>
                        {Math.round(
                          score
                        )}
                      </strong>

                      <span>
                        / 100
                      </span>
                    </div>
                  </div>
                </div>

                <div
                  className={`risk-badge ${risk}`}
                >
                  {decisionIcon(
                    decision
                  )}

                  <span>
                    {safeText(
                      level,
                      riskLabel(
                        score
                      )
                    )}
                  </span>
                </div>

                <div className="decision-box">
                  <span>
                    FINAL DECISION
                  </span>

                  <strong>
                    {safeText(
                      decision
                    )}
                  </strong>
                </div>

                {result.transaction_id && (
                  <div className="transaction-id">
                    <span>
                      TRANSACTION ID
                    </span>

                    <strong>
                      {safeText(
                        result.transaction_id
                      )}
                    </strong>
                  </div>
                )}
              </div>

              <div className="panel factors-panel">
                <PanelHeader
                  icon={
                    <Activity />
                  }
                  title="RISK CONTRIBUTIONS"
                />

                {factors.length >
                0 ? (
                  <div className="factor-list">
                    {factors.map(
                      (
                        factor
                      ) => (
                        <div
                          className="factor"
                          key={
                            factor.name
                          }
                        >
                          <div className="factor-head">
                            <span>
                              {safeText(
                                factor.name
                              ).replaceAll(
                                "_",
                                " "
                              )}
                            </span>

                            <strong>
                              {factor.value}
                            </strong>
                          </div>

                          <div className="factor-track">
                            <div
                              className={`factor-fill ${risk}`}
                              style={{
                                width: `${clamp(
                                  factor.value,
                                  0,
                                  100
                                )}%`,
                              }}
                            />
                          </div>
                        </div>
                      )
                    )}
                  </div>
                ) : (
                  <div className="empty-state">
                    No individual risk
                    contributions were
                    returned by the
                    backend.
                  </div>
                )}

                <div className="ml-box">
                  <div className="ml-icon">
                    <Cpu
                      size={18}
                    />
                  </div>

                  <div>
                    <span>
                      ML FRAUD PROBABILITY
                    </span>

                    <strong>
                      {(
                        mlProbability *
                        100
                      ).toFixed(2)}
                      %
                    </strong>
                  </div>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* =================================================
            VERIFICATION
        ================================================= */}

        {verificationOpen &&
          currentQuestion && (
            <section className="section verification-section">
              <SectionHeading
                number="03"
                title="ADAPTIVE VERIFICATION"
                subtitle="The risk engine requires contextual verification before the transaction can continue."
              />

              <div className="verification-grid">
                <div className="verification-visual">
                  <div className="verification-orbit">
                    <div className="v-ring one" />
                    <div className="v-ring two" />
                    <div className="v-ring three" />

                    <div className="v-core">
                      <LockKeyhole
                        size={35}
                      />
                    </div>

                    <div className="v-dot" />
                  </div>

                  <div className="verification-state">
                    <span>
                      SECURITY
                      CHALLENGE
                    </span>

                    <strong>
                      ACTIVE
                    </strong>
                  </div>
                </div>

                <div className="panel question-panel">
                  <div className="question-label">
                    <Sparkles
                      size={15}
                    />
                    VERIFY THIS
                    TRANSACTION
                  </div>

                  <div className="question-card">
                    <div className="question-icon">
                      <Fingerprint
                        size={19}
                      />
                    </div>

                    <div className="question-text">
                      {safeText(
                        currentQuestion.text,
                        "Please verify this transaction."
                      )}
                    </div>
                  </div>

                  <div className="voice-actions">
                    <button
                      type="button"
                      onClick={() =>
                        speakQuestion(
                          currentQuestion
                        )
                      }
                      disabled={
                        !speechAvailable
                      }
                    >
                      <Volume2
                        size={15}
                      />
                      READ QUESTION
                    </button>

                    <button
                      type="button"
                      className={
                        listening
                          ? "active"
                          : ""
                      }
                      onClick={
                        startVoiceInput
                      }
                      disabled={
                        !recognitionAvailable
                      }
                    >
                      {listening ? (
                        <MicOff
                          size={15}
                        />
                      ) : (
                        <Mic
                          size={15}
                        />
                      )}

                      {listening
                        ? "LISTENING..."
                        : "SPEAK ANSWER"}
                    </button>
                  </div>

                  {questionOptions.length >
                  0 ? (
                    <div className="answer-options">
                      {questionOptions.map(
                        (
                          option,
                          index
                        ) => {
                          const selectedOption =
                            answer ===
                            option;

                          return (
                            <button
                              key={`${option}-${index}`}
                              type="button"
                              className={
                                selectedOption
                                  ? "answer-option selected"
                                  : "answer-option"
                              }
                              onClick={() => {
                                setAnswer(
                                  option
                                );
                                setError(
                                  ""
                                );
                              }}
                            >
                              <span className="option-radio">
                                {selectedOption && (
                                  <Check
                                    size={
                                      13
                                    }
                                  />
                                )}
                              </span>

                              <span>
                                {safeText(
                                  option
                                )}
                              </span>
                            </button>
                          );
                        }
                      )}
                    </div>
                  ) : (
                    <div className="answer-input-row">
                      <input
                        type="text"
                        value={
                          answer
                        }
                        onChange={(
                          event
                        ) => {
                          setAnswer(
                            event.target
                              .value
                          );
                          setError(
                            ""
                          );
                        }}
                        onKeyDown={(
                          event
                        ) => {
                          if (
                            event.key ===
                            "Enter"
                          ) {
                            submitVerification();
                          }
                        }}
                        placeholder="Type or speak your answer..."
                      />
                    </div>
                  )}

                  <button
                    type="button"
                    className="verify-button"
                    onClick={
                      submitVerification
                    }
                    disabled={
                      verificationLoading ||
                      !answer.trim()
                    }
                  >
                    {verificationLoading ? (
                      <>
                        <RefreshCw
                          size={16}
                          className="spin"
                        />
                        VERIFYING
                      </>
                    ) : (
                      <>
                        CONFIRM ANSWER
                        <ArrowRight
                          size={16}
                        />
                      </>
                    )}
                  </button>

                  <div className="question-note">
                    <CheckCircle2
                      size={13}
                    />
                    Answers are evaluated
                    by the backend.
                    Correct answers can
                    reduce risk; failed
                    answers can trigger a
                    new challenge.
                  </div>
                </div>
              </div>
            </section>
          )}

        {/* =================================================
            PIPELINE
        ================================================= */}

        <section
          id="architecture"
          className="section"
        >
          <SectionHeading
            number="04"
            title="SECURITY PIPELINE"
            subtitle="Every payment travels through multiple intelligence layers before a final decision is produced."
          />

          <div className="pipeline">
            <PipelineNode
              icon={<UserRound />}
              number="01"
              title="IDENTITY"
              text="User profile and identity context"
            />

            <PipelineArrow />

            <PipelineNode
              icon={
                <WalletCards />
              }
              number="02"
              title="TRANSACTION"
              text="Amount, purpose and recipient"
            />

            <PipelineArrow />

            <PipelineNode
              icon={<Activity />}
              number="03"
              title="CONTEXT"
              text="Behaviour, device, network and location"
            />

            <PipelineArrow />

            <PipelineNode
              icon={<Cpu />}
              number="04"
              title="HYBRID ENGINE"
              text="Rules + machine learning"
            />

            <PipelineArrow />

            <PipelineNode
              icon={<ShieldAlert />}
              number="05"
              title="RISK"
              text="0–100 contextual risk score"
            />

            <PipelineArrow />

            <PipelineNode
              icon={
                <Fingerprint />
              }
              number="06"
              title="VERIFY"
              text="Adaptive security challenge"
            />

            <PipelineArrow />

            <PipelineNode
              icon={
                <ShieldCheck />
              }
              number="07"
              title="DECISION"
              text="Allow, monitor, hold or block"
            />
          </div>
        </section>

        {/* =================================================
            SYSTEMS
        ================================================= */}

        <section className="section systems-section">
          <SectionHeading
            number="05"
            title="SYSTEM INTELLIGENCE"
            subtitle="The platform combines multiple security signals into one explainable decision."
          />

          <div className="systems-grid">
            <SystemCard
              icon={
                <Database />
              }
              title="MONGODB"
              text="Operational transaction, identity, verification and incident data."
            />

            <SystemCard
              icon={
                <Cpu />
              }
              title="ML ENGINE"
              text="Behavioural anomaly and fraud probability analysis."
            />

            <SystemCard
              icon={
                <Radio />
              }
              title="REAL-TIME"
              text="Risk analysis occurs as the transaction is submitted."
            />

            <SystemCard
              icon={
                <AudioLines />
              }
              title="VOICE VERIFY"
              text="Browser voice input and spoken adaptive verification."
            />

            <SystemCard
              icon={
                <LockKeyhole />
              }
              title="ADAPTIVE"
              text="Verification changes according to the current risk context."
            />

            <SystemCard
              icon={
                <Zap />
              }
              title="RESPONSE"
              text="Incidents, evidence, notifications and reporting workflows."
            />
          </div>
        </section>

        {/* =================================================
            HISTORY
        ================================================= */}

        <section
          id="history"
          className="section"
        >
          <SectionHeading
            number="06"
            title="TRANSACTION HISTORY"
            subtitle="Recent transactions returned directly from the backend."
          />

          <div className="panel history-panel">
            <PanelHeader
              icon={
                <Clock3 />
              }
              title="RECENT ACTIVITY"
              status={`${transactions.length} EVENTS`}
            />

            {transactions.length ===
            0 ? (
              <div className="empty-history">
                <Database
                  size={24}
                />

                <span>
                  No transaction
                  history available
                  for this user.
                </span>
              </div>
            ) : (
              <div className="history-list">
                {transactions
                  .slice(0, 10)
                  .map(
                    (
                      transaction,
                      index
                    ) => {
                      const transactionScore =
                        numberValue(
                          transaction.risk_score ??
                            transaction.updated_risk_score ??
                            0
                        );

                      return (
                        <div
                          className="history-row"
                          key={
                            transaction.transaction_id ??
                            transaction.id ??
                            transaction._id ??
                            index
                          }
                        >
                          <div className="history-icon">
                            {transactionScore >
                            75 ? (
                              <ShieldAlert
                                size={
                                  17
                                }
                              />
                            ) : transactionScore >
                              30 ? (
                              <Activity
                                size={
                                  17
                                }
                              />
                            ) : (
                              <ShieldCheck
                                size={
                                  17
                                }
                              />
                            )}
                          </div>

                          <div className="history-main">
                            <strong>
                              {money(
                                transaction.amount
                              )}
                            </strong>

                            <span>
                              {safeText(
                                transaction.transaction_category ??
                                  transaction.category ??
                                  transaction.transaction_purpose ??
                                  "Transaction"
                              )}
                            </span>
                          </div>

                          <div className="history-location">
                            {safeText(
                              transaction.location_name ??
                                transaction.location_label ??
                                transaction.ip_location ??
                                "—"
                            )}
                          </div>

                          <div className="history-date">
                            {formatDate(
                              transaction.transaction_time ??
                                transaction.created_at
                            )}
                          </div>

                          <div
                            className={`history-score ${riskTone(
                              transactionScore
                            )}`}
                          >
                            {Math.round(
                              transactionScore
                            )}
                          </div>
                        </div>
                      );
                    }
                  )}
              </div>
            )}
          </div>
        </section>
      </main>

      {/* =================================================
          FOOTER
      ================================================= */}

      <footer className="site-footer">
        <div className="footer-brand">
          <div className="brand-mark">
            <Shield size={17} />
          </div>

          <div>
            <strong>
              SECUREFLOW-AI
            </strong>

            <span>
              INTELLIGENT PAYMENT FRAUD
              DEFENCE
            </span>
          </div>
        </div>

        <div className="footer-right">
          <span>
            RULE ENGINE
          </span>

          <span>
            ML ENGINE
          </span>

          <span>
            ADAPTIVE VERIFICATION
          </span>

          <span>
            SOC RESPONSE
          </span>
        </div>
      </footer>
    </div>
  );
}

/* =========================================================
   COMPONENTS
========================================================= */

function SpaceBackground() {
  return (
    <div className="space-background">
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />

      <div className="stars stars-one" />
      <div className="stars stars-two" />
      <div className="stars stars-three" />

      <div className="space-grid" />
    </div>
  );
}

function SectionHeading({
  number,
  title,
  subtitle,
}) {
  return (
    <div className="section-heading">
      <div className="section-number">
        {number}
      </div>

      <div>
        <div className="section-kicker">
          SECUREFLOW / SYSTEM
        </div>

        <h2>{title}</h2>

        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function PanelHeader({
  icon,
  title,
  status,
}) {
  return (
    <div className="panel-header">
      <div className="panel-title">
        <span className="panel-icon">
          {icon}
        </span>

        <span>
          {title}
        </span>
      </div>

      {status && (
        <span className="panel-status">
          {safeText(status)}
        </span>
      )}
    </div>
  );
}

function Field({
  label,
  icon,
  children,
}) {
  return (
    <label className="field">
      <span className="field-label">
        {icon}
        {label}
      </span>

      {children}
    </label>
  );
}

function TelemetryRow({
  label,
  value,
}) {
  return (
    <div className="telemetry-row">
      <span>
        {label}
      </span>

      <strong>
        {safeText(value)}
      </strong>
    </div>
  );
}

function PipelineNode({
  icon,
  number,
  title,
  text,
}) {
  return (
    <div className="pipeline-node">
      <div className="pipeline-icon">
        {icon}
      </div>

      <div className="pipeline-number">
        {number}
      </div>

      <strong>
        {title}
      </strong>

      <span>
        {text}
      </span>
    </div>
  );
}

function PipelineArrow() {
  return (
    <div className="pipeline-arrow">
      <ArrowRight size={16} />
    </div>
  );
}

function SystemCard({
  icon,
  title,
  text,
}) {
  return (
    <div className="system-card">
      <div className="system-icon">
        {icon}
      </div>

      <div className="system-title">
        {title}
      </div>

      <p>{text}</p>

      <div className="system-line" />
    </div>
  );
}

function getErrorMessage(
  error
) {
  if (!error) {
    return "An unexpected error occurred.";
  }

  if (
    typeof error ===
    "string"
  ) {
    return error;
  }

  if (
    error.message &&
    typeof error.message ===
      "string"
  ) {
    return error.message;
  }

  const data =
    error.data;

  if (
    data &&
    Array.isArray(
      data.detail
    )
  ) {
    return data.detail
      .map((item) => {
        if (
          typeof item ===
          "string"
        ) {
          return item;
        }

        return (
          item?.msg ||
          item?.message ||
          JSON.stringify(item)
        );
      })
      .join(" • ");
  }

  if (
    data &&
    typeof data ===
      "object"
  ) {
    return safeText(
      data.detail ??
        data.message ??
        data.error,
      `HTTP ${
        error.status || ""
      } request failed.`
    );
  }

  return `HTTP ${
    error.status || ""
  } request failed.`;
}