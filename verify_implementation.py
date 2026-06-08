"""
Verification script for fingerprinting implementation.
Run this to verify all changes are in place.
"""

import os
import sys
import json
from pathlib import Path

def verify_files():
    """Check if all required files exist."""
    print("\n🔍 Checking Files...")
    print("=" * 60)
    
    files_to_check = [
        "src/content_platform/fingerprint/__init__.py",
        "src/content_platform/fingerprint/unified.py",
        "src/content_platform/server/matching.py",
        "src/content_platform/edge/extractors.py",
        "src/content_platform/shared/models.py",
        "pyproject.toml",
    ]
    
    base_path = Path("d:\\Multimodel_Fingerprint")
    all_exist = True
    
    for file in files_to_check:
        full_path = base_path / file
        exists = full_path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {file}")
        if not exists:
            all_exist = False
    
    return all_exist

def verify_dependencies():
    """Check if new dependencies are in pyproject.toml."""
    print("\n🔍 Checking Dependencies...")
    print("=" * 60)
    
    required_deps = [
        "librosa",
        "mediapipe",
        "scikit-learn",
    ]
    
    toml_path = Path("d:\\Multimodel_Fingerprint\\pyproject.toml")
    
    with open(toml_path, 'r') as f:
        content = f.read()
    
    all_found = True
    for dep in required_deps:
        found = dep in content
        status = "✅" if found else "❌"
        print(f"{status} {dep}")
        if not found:
            all_found = False
    
    return all_found

def verify_code_changes():
    """Verify key code changes are in place."""
    print("\n🔍 Checking Code Changes...")
    print("=" * 60)
    
    checks = {
        "src/content_platform/fingerprint/unified.py": [
            ("UnifiedFingerprinter class", "class UnifiedFingerprinter"),
            ("visual_fingerprint method", "def visual_fingerprint"),
            ("audio_fingerprint method", "def audio_fingerprint"),
            ("cosine similarity", "np.dot(left_arr, right_arr)"),
        ],
        "src/content_platform/server/matching.py": [
            ("numpy import", "import numpy as np"),
            ("cosine similarity", "cosine_sim = np.dot"),
            ("MFCC audio handling", "ae-hash-"),
        ],
        "src/content_platform/edge/extractors.py": [
            ("Batch buffering", "_buffer_frames"),
            ("Simulated batching", "_capture_simulated_buffered"),
            ("Pi batching", "_capture_pi_buffered"),
            ("Batch processing", "_process_batch_pi"),
        ],
        "src/content_platform/shared/models.py": [
            ("batch_count field", "batch_count: int"),
            ("batch_duration_sec field", "batch_duration_sec: int"),
            ("confidence_visual field", "confidence_visual: float"),
            ("confidence_audio field", "confidence_audio: float"),
        ],
    }
    
    all_verified = True
    base_path = Path("d:\\Multimodel_Fingerprint")
    
    for file_path, patterns in checks.items():
        full_path = base_path / file_path
        
        if not full_path.exists():
            print(f"❌ {file_path} NOT FOUND")
            all_verified = False
            continue
        
        with open(full_path, 'r') as f:
            content = f.read()
        
        print(f"\n📄 {file_path}:")
        for check_name, pattern in patterns:
            found = pattern in content
            status = "✅" if found else "❌"
            print(f"  {status} {check_name}")
            if not found:
                all_verified = False
    
    return all_verified

def main():
    """Run all verification checks."""
    print("\n" + "=" * 60)
    print("FINGERPRINTING IMPLEMENTATION VERIFICATION")
    print("=" * 60)
    
    results = {
        "Files": verify_files(),
        "Dependencies": verify_dependencies(),
        "Code Changes": verify_code_changes(),
    }
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    all_passed = all(results.values())
    
    for check, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {check}")
    
    print("\n" + "=" * 60)
    
    if all_passed:
        print("✅ ALL CHECKS PASSED!")
        print("\nNext steps:")
        print("1. Install dependencies: pip install -e .")
        print("2. Run tests: pytest -v")
        print("3. Proceed to Phase 5: Library reseeding")
        return 0
    else:
        print("❌ SOME CHECKS FAILED!")
        print("Please review the failed items above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
