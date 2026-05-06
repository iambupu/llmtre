export type ApiSuccess<T> = T & { ok: true; trace_id: string };

export type ApiFailure = {
  ok: false;
  trace_id?: string;
  error?: { code?: string; message?: string };
  trace?: unknown;
};

export type SessionPayload = {
  session_id: string;
  current_session_turn_id?: number;
  sandbox_mode?: boolean;
  active_character?: Record<string, unknown> | null;
  scene_snapshot?: SceneSnapshot | null;
  memory_summary?: string;
};

export type SceneAffordance = {
  id: string;
  label: string;
  action_type: string;
  enabled: boolean;
  reason?: string;
  user_input?: string;
  target_id?: string | null;
  location_id?: string | null;
  object_id?: string | null;
  slot_id?: string | null;
  priority?: number;
};

export type InteractionSlot = {
  slot_id: string;
  object_id: string;
  action_type: string;
  label: string;
  enabled: boolean;
  disabled_reason?: string;
  default_input?: string;
  required_params?: string[];
};

export type SceneObjectRef = {
  object_id: string;
  object_type: string;
  label: string;
  description?: string;
  state_tags?: string[];
  source_ref?: Record<string, unknown>;
  priority?: number;
};

export type SceneSnapshot = {
  schema_version?: string;
  current_location?: Record<string, unknown>;
  exits?: Record<string, unknown>[];
  visible_npcs?: Record<string, unknown>[];
  visible_items?: Record<string, unknown>[];
  active_quests?: Record<string, unknown>[];
  recent_memory?: string;
  available_actions?: string[];
  suggested_actions?: string[];
  scene_objects?: SceneObjectRef[];
  interaction_slots?: InteractionSlot[];
  affordances?: SceneAffordance[];
  ui_hints?: Record<string, unknown>;
};

export type TurnResult = {
  session_id: string;
  session_turn_id: number;
  runtime_turn_id: number;
  trace_id: string;
  request_id: string;
  final_response: string;
  quick_actions: string[];
  affordances: SceneAffordance[];
  active_character?: Record<string, unknown> | null;
  scene_snapshot?: SceneSnapshot | null;
  memory_summary?: string;
  debug_trace?: unknown[];
  outcome?: string;
  failure_reason?: string;
  suggested_next_step?: string;
  should_advance_turn?: boolean;
  should_write_story_memory?: boolean;
  errors?: string[];
  trace?: unknown;
};

export type StreamEventPayload = Record<string, unknown>;
