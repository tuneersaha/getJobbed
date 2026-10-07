export interface GapAnalysis {
  matching_skills: string[]
  missing_skills: string[]
  skills_coverage: number
  experience_gap?: string
}

export interface JobListItem {
  match_id: string
  job_id: string
  title: string
  company_name: string
  location: string | null
  work_type: string
  match_score: number
  status: string
  gap_analysis: GapAnalysis
  has_tailored_resume: boolean
  tailoring_failed: boolean
  posted_at: string | null
}

export interface JobListResponse {
  jobs: JobListItem[]
  total: number
  has_more: boolean
}

export interface JobDetail extends JobListItem {
  description: string
  apply_url: string
}

export interface TailoredResumeResponse {
  latex_source: string
  prompt_tokens: number
  completion_tokens: number
  model_used: string
}

export interface AppliedListItem {
  match_id: string
  job_id: string
  title: string
  company_name: string
  location: string | null
  work_type: string
  match_score: number
  status: string
  gap_analysis: GapAnalysis
  applied_at: string | null
  posted_at: string | null
}

export interface AppliedListResponse {
  jobs: AppliedListItem[]
  total: number
  has_more: boolean
}

export interface ProfileResponse {
  desired_roles: string[]
  experience_years: number
  experience_level: string
  max_experience_years: number
  locations: string[]
  remote_ok: boolean
  excluded_keywords: string[]
  tailor_threshold: number
}

export interface StatsResponse {
  last_fetch_at: string | null
  jobs_found_today: number
  queue_depth: number
  tailoring_in_progress: number
  tailoring_failed: number
  total_matched: number
  total_applied: number
  cumulative_prompt_tokens: number
  cumulative_completion_tokens: number
}

export interface ResumeMetadata {
  resume_id: string
  uploaded_at: string
  parsed_skills: string[]
}

export interface FetchTriggerResponse {
  task_ids: number[]
  queued_at: string
  sources: string[]
}
