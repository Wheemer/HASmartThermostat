"""Durable gain-change handoff; never actuates heating or changes setpoints."""

from copy import deepcopy


async def commit_gain_change(proposal, engine, now, read_context, save, apply, rollback=False):
    """Persist intent before changing gains; recheck user controls after IO.

    read_context returns gains, target, mode and eligible. save receives a
    journal (or None) and the learning state. apply is synchronous so there is
    no event-loop yield between the final context check and gain assignment.
    """
    before_context = deepcopy(read_context())
    if not before_context['eligible'] or before_context['gains'] != proposal['old']:
        return 'deferred'
    before_learning = engine.snapshot()
    staged_engine = deepcopy(engine)
    if rollback:
        staged_engine.rollback_committed(now)
    else:
        staged_engine.committed(proposal, now)
    after_learning = staged_engine.snapshot()
    journal = {'version': 1, 'old': proposal['old'], 'new': proposal['new'],
               'before': before_learning, 'after': after_learning}
    # If this write fails, nothing has changed in the controller.
    await save(deepcopy(journal), before_learning)
    if read_context() != before_context:
        await save(None, before_learning)
        return 'deferred'
    apply(dict(proposal['new']))
    engine.pending = staged_engine.pending
    engine.last_change = staged_engine.last_change
    engine.reason = staged_engine.reason
    # On failure, the durable journal still describes the exact old/new states.
    await save(None, after_learning)
    return 'rolled_back' if rollback else 'applied'


def recover_gain_change(journal, gains):
    """Choose the saved learning state matching actually restored PID gains."""
    if not isinstance(journal, dict) or journal.get('version') != 1:
        raise ValueError('Invalid gain transaction journal')
    if gains == journal.get('old'):
        return deepcopy(journal['before'])
    if gains == journal.get('new'):
        return deepcopy(journal['after'])
    raise ValueError('Restored gains do not match the transaction; do not overwrite them')
