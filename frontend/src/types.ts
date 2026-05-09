export type ApiSuccess<T> = T & { ok: true; trace_id: string };

export type ApiFailure = {
  ok: false;
  trace_id?: string;
  error?: { code?: string; message?: string };
  trace?: unknown;
};

export type CharacterStatusEffect = {
  key: string;
  label: string;
  kind: string;
  severity: string;
  description: string;
};

export type CharacterStatusContext = {
  resource_state: string;
  flags: string[];
  prompt_text: string;
};

export type ActiveCharacter = Record<string, unknown> & {
  id?: string;
  character_id?: string;
  name?: string;
  label?: string;
  hp?: number;
  max_hp?: number;
  mp?: number;
  max_mp?: number;
  inventory?: unknown[];
  inventory_items?: unknown[];
  location?: string;
  state_flags?: string[];
  status_summary?: string;
  status_effects?: CharacterStatusEffect[];
  status_context?: CharacterStatusContext;
};

export type SessionPayload = {
  session_id: string;
  character_id?: string;
  current_session_turn_id?: number;
  sandbox_mode?: boolean;
  pack_id?: string | null;
  scenario_id?: string | null;
  pack_version?: string | null;
  compiled_artifact_hash?: string | null;
  persona_profile?: Record<string, unknown>;
  quick_actions?: string[];
  quick_action_candidates?: Array<{
    canonical_intent_key: string;
    target_object_hint: string;
    display_text: string;
    confidence?: number | null;
    reason?: string;
  }>;
  quick_action_groups?: { current?: string[]; nearby?: string[] };
  quick_action_layout?: {
    common_actions?: string[];
    object_actions?: Record<string, string[]>;
    diagnostics?: Record<string, unknown>;
  };
  active_character?: ActiveCharacter | null;
  scene_snapshot?: SceneSnapshot | null;
  memory_summary?: string;
};

export type StoryPackSummary = {
  pack_id: string;
  title: string;
  version: string;
  scenario_id: string;
  start_scene_id: string;
  compiled_artifact_hash: string;
  scene_count: number;
  interaction_count: number;
  diagnostics: string[];
};

export type StoryPackListPayload = {
  packs: StoryPackSummary[];
  diagnostics: Record<string, string[]>;
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
  quick_action_candidates?: Array<{
    canonical_intent_key: string;
    target_object_hint: string;
    display_text: string;
    confidence?: number | null;
    reason?: string;
  }>;
  quick_action_groups?: { current?: string[]; nearby?: string[] };
  quick_action_layout?: {
    common_actions?: string[];
    object_actions?: Record<string, string[]>;
    diagnostics?: Record<string, unknown>;
  };
  affordances: SceneAffordance[];
  active_character?: ActiveCharacter | null;
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
