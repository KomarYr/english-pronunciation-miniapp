/* Types mirroring the JSON the Python backend serializes (api.py). */

export interface Exercise {
  id: string;
  title: string;
  reference_text: string;
  category: string;
  level: string | null;
  phoneme_focus: string[];
}

export type WordErrorType = "None" | "Mispronunciation" | "Omission" | "Insertion";

export interface WordScore {
  word: string;
  accuracy_score: number;
  error_type: WordErrorType;
  phonemes: [string, number][];
}

export interface ScoreReport {
  recognized_text: string;
  accuracy_score: number;
  fluency_score: number;
  completeness_score: number;
  prosody_score: number;
  pronunciation_score: number;
  words: WordScore[];
}

export interface Attempt {
  id: number | null;
  exercise_id: string | null;
  reference_text: string;
  created_at: string;
  audio_seconds: number;
  report: ScoreReport;
}

export interface Mistake {
  exercise: Exercise;
  avg_pronunciation_score: number;
  attempt_count: number;
  last_attempted: string;
}

export interface Quota {
  used_seconds: number;
  limit_seconds: number;
  remaining_seconds: number;
  fraction_used: number;
  warn: boolean;
  exhausted: boolean;
}

export interface UserStats {
  total_attempts: number;
  total_audio_seconds: number;
  avg_pronunciation_score: number | null;
  avg_accuracy_score: number | null;
}

export interface Me {
  user: { id: number; display_name: string; telegram_id: number };
  dev_mode: boolean;
}
