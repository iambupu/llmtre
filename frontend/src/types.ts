export type ApiSuccess<T> = T & { ok: true; trace_id: string };

export type ApiFailure = {
  ok: false;
  trace_id?: string;
  error?: { code?: string; message?: string };
  trigger_events?: unknown[];
  quest_updates?: unknown[];
  pack_quests?: unknown[];
  pack_triggers?: unknown[];
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
  runtime_character_id?: string;
  current_session_turn_id?: number;
  sandbox_mode?: boolean;
  pack_id?: string | null;
  scenario_id?: string | null;
  pack_version?: string | null;
  compiled_artifact_hash?: string | null;
  current_scene_id?: string | null;
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
  created_at?: string;
  last_active_at?: string;
};

export type SessionSummary = {
  session_id: string;
  character_id?: string;
  base_character_id?: string;
  runtime_character_id?: string;
  sandbox_mode?: boolean;
  current_session_turn_id: number;
  memory_summary?: string;
  pack_id?: string | null;
  pack_title?: string | null;
  scenario_id?: string | null;
  pack_version?: string | null;
  compiled_artifact_hash?: string | null;
  current_scene_id?: string | null;
  current_scene_title?: string | null;
  created_at?: string;
  last_active_at?: string;
  updated_at?: string;
};

export type SessionListPayload = {
  items: SessionSummary[];
  total: number;
  limit: number;
};

export type DeleteSessionPayload = {
  deleted_session_id: string;
  character_id?: string;
  base_character_id?: string;
  runtime_character_id?: string;
  deleted_sessions?: number;
  deleted_turns?: number;
  deleted_memory_items?: number;
  deleted_idempotency_keys?: number;
  deleted_shadow_state?: boolean;
  runtime_character_owned?: boolean;
  deleted_entities_active?: number;
  deleted_inventory_active?: number;
  deleted_entities_shadow?: number;
  deleted_inventory_shadow?: number;
  deleted_agent_memory?: boolean;
};

export type TurnSummary = {
  session_turn_id: number;
  is_valid: boolean;
  user_input: string;
  final_response: string;
  created_at: string;
};

export type TurnListPayload = {
  session_id: string;
  page: number;
  page_size: number;
  total: number;
  items: TurnSummary[];
};

export type StoryPackSummary = {
  pack_id: string;
  title: string;
  version: string;
  scenario_id: string;
  start_scene_id: string;
  start_scene_title: string;
  compiled_artifact_hash: string;
  source_background_hash?: string | null;
  scene_count: number;
  interaction_count: number;
  quest_count: number;
  trigger_count: number;
  asset_count: number;
  diagnostics: string[];
};

export type StoryPackListPayload = {
  packs: StoryPackSummary[];
  diagnostics: Record<string, string[]>;
};

export type StoryPackDetailPayload = {
  summary: StoryPackSummary;
  manifest: Record<string, unknown>;
  scenes: Record<string, unknown>[];
};

export type StoryPackImportPayload = {
  manifest: Record<string, unknown>;
  scenes: Record<string, Record<string, unknown>> | Record<string, unknown>[];
  lore?: Record<string, string>;
  quests?: Record<string, Record<string, unknown>> | Record<string, unknown>[];
  triggers?: Record<string, Record<string, unknown>> | Record<string, unknown>[];
  asset_files?: Record<string, string>;
};

export type StoryPackImportResult = {
  summary: StoryPackSummary;
};

export type StoryPackDeletePayload = {
  deleted_pack_id: string;
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
  asset_url?: string;
  background_asset_url?: string;
  image_asset_url?: string;
  portrait_asset_url?: string;
  icon_asset_url?: string;
  priority?: number;
};

export type SceneAssetRef = {
  asset_id: string;
  kind: string;
  media_type?: "image" | "gif" | "video" | "audio" | string | null;
  src: string;
  url?: string;
  alt?: string;
  caption?: string | null;
  mime_type?: string | null;
  playback?: {
    mode?: "manual" | "once" | "loop" | string | null;
    controls?: boolean | null;
    muted?: boolean | null;
    preload?: "none" | "metadata" | "auto" | string | null;
    volume?: number | null;
    start_time_seconds?: number | null;
    end_time_seconds?: number | null;
  } | null;
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
  assets?: Record<string, SceneAssetRef>;
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
  trigger_events?: unknown[];
  quest_updates?: unknown[];
  quest_states?: unknown[];
  branch_consequences?: unknown[];
  pack_runtime_errors?: unknown[];
  pack_quests?: unknown[];
  pack_triggers?: unknown[];
  trace?: unknown;
};

export type StreamEventPayload = Record<string, unknown>;
