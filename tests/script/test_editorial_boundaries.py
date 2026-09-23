"""Regressions for execution ordering that a language-model JSON draft can violate."""
import copy
from test_editorial_contract import fixture,modules


def test_replay_of_future_evidence_is_rejected_before_tts():
    project,evidence,style,story=fixture()
    story['paragraphs'][0]['presentation'][0].update(type='replay',audio='mute',return_to='o2')
    errors=modules()[0].validate_story(story,project,evidence,style)
    assert any(e['code']=='replay_unseen' for e in errors)


def test_return_to_previous_operation_is_not_a_story_exit():
    project,evidence,style,story=fixture()
    second=story['paragraphs'][1];second['presentation'][0].update(type='replay',source_start=52,source_end=54,audio='mute',return_to='o1');second['protected_audio']=[]
    errors=modules()[0].validate_story(story,project,evidence,style)
    assert any(e['code']=='replay_return' for e in errors)


def test_missing_allowed_window_cannot_implicitly_shift_narration():
    project,evidence,style,story=fixture();story['paragraphs'][0]['narration']['allowed_operations']=[]
    errors=modules()[0].validate_story(story,project,evidence,style)
    assert any(e['code']=='narration_window' for e in errors)


def test_style_version_change_cannot_reuse_old_story():
    project,evidence,style,story=fixture();style['profile']['version']=2
    assert any(e['code']=='style_version' for e in modules()[0].validate_story(story,project,evidence,style))


def test_project_mandated_original_scene_cannot_be_omitted_from_protection():
    project,evidence,style,story=fixture();project['required_audio_ranges']=[{'start':61,'end':65,'purpose':'关键对白'}]
    story['paragraphs'][1]['protected_audio']=[]
    assert any(e['code']=='required_audio_missing' for e in modules()[0].validate_story(story,project,evidence,style))


def test_estimated_speech_cannot_cover_declared_protected_scene():
    project,evidence,style,story=fixture();n=story['paragraphs'][0]['narration'];n.update(offset=9,allowed_operations=['o1','o2'],text='现在画面里发生的事情，需要很长的一段话才能讲完。')
    assert any(e['code']=='estimated_audio_overlap' for e in modules()[0].validate_story(story,project,evidence,style))


def test_bad_after_event_dict_returns_expected_field_names():
    project,evidence,style,story=fixture();story['paragraphs'][0]['narration']['after_events']=[{'event':'门锁转了','at':55}]
    errors=modules()[0].validate_story(story,project,evidence,style)
    assert any(e['code']=='event_anchor_shape' and 'operation_id' in e['message'] for e in errors)


def test_unknown_execution_field_rejected_instead_of_silently_ignored():
    project,evidence,style,story=fixture();story['paragraphs'][0]['presentation'][0]['sound_effect']='door.wav'
    assert any(e['code']=='unsupported_operation' for e in modules()[0].validate_story(story,project,evidence,style))


def test_deferred_question_cannot_be_simultaneously_answered():
    project,evidence,style,story=fixture();story['questions'][0].update(status='deferred',next_part='下集')
    assert any(e['code']=='question_state' for e in modules()[0].validate_story(story,project,evidence,style))


def test_word_evidence_ids_are_usable_by_target_claims():
    project,evidence,style,story=fixture();evidence['records'].append({'id':'asr:0','kind':'dialogue','source_id':'film-B','words':[{'evidence_id':'asr:0:word:0','start':55,'end':56,'text':'走'}]})
    story['paragraphs'][0]['claims'][0]['evidence_ids']=['asr:0:word:0']
    assert modules()[0].validate_story(story,project,evidence,style)==[]


def test_unhashable_identifiers_return_precise_findings_not_exception():
    project,evidence,style,story=fixture();story['paragraphs'][0]['claims'][0]['id']={}
    errors=modules()[0].validate_story(story,project,evidence,style)
    assert any(e['code']=='invalid_shape' and 'claims[0].id' in e['path'] for e in errors)


def test_anchor_offset_and_frame_minimum_checked_before_tts():
    project,evidence,style,story=fixture();story['paragraphs'][0]['narration']['offset']=11
    assert any(e['code']=='narration_window' for e in modules()[0].validate_story(story,project,evidence,style))
    project,evidence,style,story=fixture();story['paragraphs'][0]['presentation'][0]['source_end']=50.001
    assert any(e['code']=='frame_duration' for e in modules()[0].validate_story(story,project,evidence,style))


def test_long_text_at_end_of_window_is_rejected_before_tts():
    project,evidence,style,story=fixture();n=story['paragraphs'][0]['narration'];n.update(offset=9.5,text='门锁虽然转了一圈，但是这扇门还是没有打开。')
    assert any(e['code']=='estimated_window_overrun' for e in modules()[0].validate_story(story,project,evidence,style))
