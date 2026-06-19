# Confidence tuning thresholds and weightings for content matching

# Platform & Channel Logo Matching
LOGO_MATCH_THRESHOLD = 0.65
LAYOUT_MATCH_THRESHOLD = 0.65

# Server-Side Logo Extraction
LOGO_ROI_MIN_STD = 15.0

# Modality Scores Combination Weights
VISUAL_SCORE_WEIGHT = 0.70
AUDIO_SCORE_WEIGHT = 0.30

# Content Matching Thresholds
MATCH_THRESHOLD_MODALITIES_STRONG = 0.60  # Threshold when visual match is strong and audio is present
MATCH_THRESHOLD_MODALITIES_WEAK = 0.75    # Threshold when visual match is weaker or audio is missing
MATCH_THRESHOLD_CONTINUOUS = 0.50          # Lowered threshold for temporal progression locks (with audio)
MATCH_THRESHOLD_INTRO = 0.80               # Stricter threshold for dedicated intro segments

# Shared Intro Detection
SHARED_INTRO_MATCH_THRESHOLD = 0.80        # Min score to count as matching episode intro
SHARED_INTRO_MIN_EPISODES = 4              # Min matching episodes of the same series to trigger generic series intro
SHARED_INTRO_MAX_OFFSET = 150              # Max playback position (in seconds) to consider for shared intro/outro detection

