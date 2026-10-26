"""Opt-in, exact controlled solver validation using the durable trial machinery.

The worker's success flag is never scientific authorization: completion reloads
the library's persisted independent proof inside the job completion transaction.
"""
from app import config
from app.skill_drafts import DraftError
from app.skill_spec import prepare_skill_spec
from app.skill_validation_jobs import ValidationJobs, _hash, _now, canonical
from app.skill_validation_worker import ValidationWorker


class GeneralValidationJobs(ValidationJobs):
    def __init__(self, store, runtime_sha256, case, library):
        super().__init__(store, runtime_sha256)
        from app.skill_general_profiles import policy
        self.case, self.library = case, library
        self.policy = policy(case, runtime_sha256)
        self.policy_json = canonical(self.policy)
        self.profile_id = self.policy['profile_id']

    @staticmethod
    def _enabled():
        ValidationJobs._enabled()
        if getattr(config, 'SKILL_GENERAL_ADMISSION_ENABLED', False) is not True:
            raise DraftError('general_admission_disabled')

    @staticmethod
    def _intent(intent):
        if intent != 'validate':
            raise DraftError('invalid_general_intent')
        return intent

    def install_trial_profile(self):
        raise DraftError('general_profile_registration_required')

    def _profile(self, db):
        return self.library.require_profile(db, self.case, self.runtime_sha256)

    def _view(self, db, row):
        view = super()._view(db, row)
        from app.skill_general_profiles import build_controlled_spec, match_profile
        prepared = prepare_skill_spec(canonical(view['spec']))
        if match_profile(prepared) != self.case:
            raise DraftError('profile_case_mismatch')
        controlled = build_controlled_spec(canonical(view['spec']))
        # A different template must be saved visibly as a new draft version.
        if controlled['code_template'] != view['spec']['code_template']:
            raise DraftError('controlled_version_required')
        if (row['intent'] != 'validate' or row['profile_id'] != self.profile_id
                or row['profile_version'] != 1):
            raise DraftError('profile_not_available')
        return view

    def enqueue(self, draft_id, *, actor_id, expected_revision, idempotency_key, intent='validate'):
        return super().enqueue(draft_id, actor_id=actor_id,
                               expected_revision=expected_revision,
                               idempotency_key=idempotency_key, intent=intent)

    def _complete(self, db, row, manifest_sha256, receipt_sha256, succeeded, reason):
        _hash(receipt_sha256)
        if type(succeeded) is not bool:
            raise DraftError('invalid_result')
        if reason not in ('general_validation_complete', 'executor_failed', 'invalid_result', 'result_storage_failed'):
            raise DraftError('invalid_reason')
        view = self._view(db, row)
        cancelled = row['cancel_ms'] is not None
        passed = False
        if succeeded and not cancelled:
            _hash(manifest_sha256)
            self._profile(db)
            passed = self.library.verify_proof(db, view, manifest_sha256, receipt_sha256)
            if type(passed) is not bool:
                raise DraftError('invalid_validation_proof')
        state = 'cancelled' if cancelled else 'validated' if passed else 'rejected'
        reason = ('cancelled_after_execution' if cancelled else
                  'independent_validation_passed' if passed else
                  'independent_validation_failed' if succeeded else reason)
        now = _now()
        db.execute('''UPDATE skill_validation_attempts SET dispatch_state='terminal',receipt_sha256=?,
            output_manifest_sha256=?,updated_ms=? WHERE job_id=? AND attempt_no=?''',
                   (receipt_sha256, manifest_sha256, now, row['job_id'], row['attempt_no']))
        db.execute('''UPDATE skill_validation_jobs SET state=?,reason_code=?,revision=revision+1,
            lease_owner=NULL,lease_until_ms=NULL,updated_ms=? WHERE job_id=?''',
                   (state, reason, now, row['job_id']))
        self._release_draft(db, row, now)
        return self._view(db, self._row(db, row['job_id']))


class GeneralValidationWorker(ValidationWorker):
    def __init__(self, jobs, assets, library, **kwargs):
        super().__init__(jobs, assets, **kwargs)
        if jobs.library is not library:
            raise ValueError('validation_library_mismatch')
        self.library = library

    def _bind(self, job):
        bound = super()._bind(job)
        if not bound['succeeded']:
            return bound
        # Includes the original immutable history, receipt and full result binding
        # checked above; independent comparison never accepts stdout PASS.
        report = self.library.validate_result(job, bound['manifest_sha256'])
        _hash(report['proof_sha256'])
        if type(report['passed']) is not bool:
            raise DraftError('invalid_validation_proof')
        bound['reason'] = 'general_validation_complete'
        return bound
