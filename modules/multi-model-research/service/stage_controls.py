"""One-stage controls. Never change a sibling or the run's pause policy."""
import asyncio
from workflow import ATTENTION, ready, refresh_status

class StageControls:
    @staticmethod
    def stage_snapshot(run,stage):
        return {'scope':'stage','run_paused':run['paused'],'run_control_state':run.get('control_state'),
                'stage':[stage['id'],stage['status'],stage['attempts'],stage.get('control_state')]}

    def start_stage_stop(self,run_id,stage_id,target):
        key=(run_id,stage_id)
        if key in self.control_tasks:return
        task=asyncio.create_task(self.finish_stage_stop(run_id,stage_id,target))
        self.control_tasks[key]=task
        task.add_done_callback(lambda _:self.control_tasks.pop(key,None))

    async def finish_stage_stop(self,run_id,stage_id,target):
        from engine import ServiceError
        error=None;run=await self.get(run_id);stage=self.stage(run,stage_id)
        if stage['status']!='completed':
            try:
                if stage['mode']=='account' and stage.get('request_dispatched'):
                    await self.provider.cancel(stage)
                task=self.tasks.get((run_id,stage_id))
                if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
            except Exception as exc:
                error=str(exc) if isinstance(exc,ServiceError) else 'Aşama işleminin durduğu doğrulanamadı.'
        async with self.lock:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            if stage['status']=='completed':stage.update(control_state=None,control_error=None)
            else:stage.update(control_state='stop_failed' if error else target,status='stop_failed' if error else target,
                              control_error=error,next_retry_at=None)
            refresh_status(run);await self.store.save(run)
        self.schedule_sync(run_id)

    async def stage_action(self,run_id,stage_id,action,expected_state=None):
        from engine import ServiceError
        if action not in ('pause','stop','cancel','resume','restore','retry'):raise ServiceError('Bilinmeyen aşama işlemi.')
        async with self.lock:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            self.check_control_snapshot(run,expected_state,stage_id)
            if run.get('control_state'):raise ServiceError('Önce tüm araştırmanın durdurma durumunu çözün.',409)
            if stage['status']=='completed':raise ServiceError('Tamamlanan aşama ve raporu değiştirilmez.',409)
            held=stage.get('control_state')
            if action in ('pause','stop','cancel'):
                if held=='stopping':return run
                if held=='cancelled':raise ServiceError('Vazgeçilen aşama önce geri yüklenmelidir.',409)
                target={'pause':'paused','stop':'stopped','cancel':'cancelled'}[action]
                stage.update(control_state='stopping',control_target=target,next_retry_at=None,control_error=None)
                # Keep active status until cancellation settles; progress remains truthful.
                refresh_status(run);await self.store.save(run)
                self.start_stage_stop(run_id,stage_id,target)
                return run
            if run['paused']:raise ServiceError('Tüm araştırma duraklatılmış. Önce üstteki araştırma kontrolünden devam edin.',409)
            if held in ('stopping','stop_failed'):raise ServiceError('Önce bu aşamanın durduğunu doğrulayın.',409)
            if held=='cancelled' and action!='restore':raise ServiceError('Bu aşama açıkça geri yüklenmelidir.',409)
            if action=='restore' and held!='cancelled':raise ServiceError('Bu aşama vazgeçilmiş değil.',409)
            if stage['status']=='running' or (run_id,stage_id) in self.tasks:raise ServiceError('Bu aşama zaten çalışıyor.',409)
            if action=='retry' and (stage['status'] not in ATTENTION or held):raise ServiceError('Bu aşama tekrar denemeye uygun değil.',409)
            if action=='resume' and held not in ('paused','stopped'):raise ServiceError('Bu aşama duraklatılmış veya durdurulmuş değil.',409)
            if stage['mode']=='import' and action=='retry':raise ServiceError('İçe aktarma görevi otomatik tekrarlanmaz.',409)
            stage.update(control_state=None,control_error=None,error=None,retry_index=0,next_retry_at=None,
                         status=('waiting_input' if stage['mode']=='import' else 'ready') if ready(run,stage) else 'pending')
            refresh_status(run);await self.store.save(run)
        await self.kick(run_id,only=stage_id)
        return await self.get(run_id)
