# pylint: disable=all

"""
Final test cases for verification_codes.py to achieve 100% coverage.
Tests complex update flows, retry logic, and specific error handling branches.
"""

import pytest
import uuid
import time
import jwt
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from apps.user_service.app.api.verification_codes import (
    _update_email_or_phone,
    VerificationTrigger,
)

@pytest.mark.asyncio
async def test_update_email_full_flow_with_retry():
    """
    Test complete email update flow including:
    - Session creation
    - Metadata update
    - Main email update
    - Verification failure (retry trigger)
    - Retry success
    - Organization members update
    """
    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user = MagicMock()
    # Mock user dict method for line 561 coverage (hasattr dict but not model_dump)
    del mock_user.model_dump 
    mock_user.dict.return_value = {"id": "user-123", "email": "old@example.com"}
    mock_user_response.user = mock_user
    
    current_time = int(time.time())
    future_time = current_time + 3600
    
    # Setup mock admin client structure explicitly
    mock_admin_client = MagicMock()
    mock_admin_auth = MagicMock()
    mock_admin_api = MagicMock()
    
    mock_admin_client.auth = mock_admin_auth
    mock_admin_auth.admin = mock_admin_api
    
    # Responses for update_user_by_id calls
    update_success_response = MagicMock()
    update_success_response.user = MagicMock()
    update_success_response.user.email = "new@example.com"
    
    old_user_response = MagicMock()
    old_user_response.user.email = "old@example.com"
    old_user_response.user.user_metadata = {"email": "old@example.com"}
    
    new_user_response = MagicMock()
    new_user_response.user.email = "new@example.com"
    new_user_response.user.user_metadata = {"email": "new@example.com"}

    with patch('apps.user_service.app.api.verification_codes._get_supabase_client_with_token',
               AsyncMock(return_value=mock_client)), \
         patch.object(mock_client.auth, 'get_user', AsyncMock(return_value=mock_user_response)), \
         patch('apps.user_service.app.api.verification_codes.os.getenv', return_value="secret"), \
         patch('apps.user_service.app.api.verification_codes.jwt.decode') as mock_decode, \
         patch('libs.shared_db.supabase_db.db.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)), \
         patch('supabase_auth.types.Session') as mock_session, \
         patch('supabase_auth.types.User') as mock_user_class, \
         patch('supabase_auth.helpers.model_dump_json') as mock_dump_json:
            
        mock_decode.return_value = {"sub": "user-123", "exp": future_time}
        mock_user_class.return_value = mock_user
        mock_session.return_value = MagicMock()
        mock_dump_json.return_value = "{}"
        
        # Session persistence setup (coverage for line 594)
        mock_client.auth._storage_key = "test-key"
        mock_client.auth._persist_session = True
        mock_client.auth._storage.set_item = AsyncMock()
        
        # Mock admin client sequence
        # Be very specific with the mock method to avoid AttributeError
        mock_admin_api.get_user_by_id = AsyncMock(side_effect=[
            old_user_response,  # Initial metadata fetch
            old_user_response,  # Verification check (still old email -> triggers retry)
            new_user_response   # Check during/after retry
        ])
        
        mock_admin_api.update_user_by_id = AsyncMock(return_value=update_success_response)
        
        # Organization members update mock
        mock_org_query = MagicMock()
        mock_org_update = MagicMock()
        mock_org_eq = MagicMock()
        mock_org_execute = AsyncMock(return_value=MagicMock(data=[{"id": 1}]))
        
        mock_admin_client.table.return_value = mock_org_query
        mock_org_query.update.return_value = mock_org_update
        mock_org_update.eq.return_value = mock_org_eq
        mock_org_eq.execute = mock_org_execute

        email_updated, phone_updated = await _update_email_or_phone(
            "user-123",
            "new@example.com",
            VerificationTrigger.EMAIL_UPDATE.value,
            "fake-token"
        )
        
        assert email_updated is True
        assert phone_updated is False
        
        # Verify retry was called (update_user_by_id called multiple times)
        assert mock_admin_api.update_user_by_id.call_count >= 2
        # Verify session persistence was called
        mock_client.auth._storage.set_item.assert_called_once()

@pytest.mark.asyncio
async def test_update_phone_full_flow_with_plus_stripping():
    """
    Test phone update where Supabase strips the '+' sign, triggering verification warning/retry.
    Also tests metadata update failure (non-blocking).
    """
    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()
    
    # Setup mock admin client structure
    mock_admin_client = MagicMock()
    mock_admin_auth = MagicMock()
    mock_admin_api = MagicMock()
    mock_admin_client.auth = mock_admin_auth
    mock_admin_auth.admin = mock_admin_api
    
    # Phone numbers
    input_phone = "+1234567890"
    stripped_phone = "1234567890"
    
    # Response simulating stripped phone
    update_stripped_response = MagicMock()
    update_stripped_response.user.phone = stripped_phone
    update_stripped_response.user.user_metadata = {"phone": stripped_phone}
    
    # Response simulating correct phone (after retry/fix)
    update_correct_response = MagicMock()
    update_correct_response.user.phone = input_phone
    update_correct_response.user.user_metadata = {"phone": input_phone}

    with patch('apps.user_service.app.api.verification_codes._get_supabase_client_with_token',
               AsyncMock(return_value=mock_client)), \
         patch.object(mock_client.auth, 'get_user', AsyncMock(return_value=mock_user_response)), \
         patch('apps.user_service.app.api.verification_codes.os.getenv', return_value="secret"), \
         patch('apps.user_service.app.api.verification_codes.jwt.decode', return_value={"exp": time.time() + 3600}), \
         patch('libs.shared_db.supabase_db.db.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)), \
         patch('supabase_auth.types.Session'), \
         patch('supabase_auth.types.User'), \
         patch('supabase_auth.helpers.model_dump_json', return_value="{}"):
        
        # Session persistence setup - Critical for fixing the TypeError
        mock_client.auth._persist_session = True
        mock_client.auth._storage.set_item = AsyncMock()
        
        # Setup calls
        mock_admin_api.get_user_by_id = AsyncMock(side_effect=[
            MagicMock(user=MagicMock(user_metadata={})), # Initial metadata
            update_stripped_response, # Verification check (stripped)
        ])
        
        # Update sequence
        mock_admin_api.update_user_by_id = AsyncMock(side_effect=[
            update_stripped_response,
            Exception("Metadata update failed"), # Line 784 coverage
            update_stripped_response, # Metadata retry (line 798)
            update_correct_response   # Phone retry (line 819)
        ])
        
        # Org update failure (coverage line 846)
        mock_org_query = MagicMock()
        mock_admin_client.table.return_value = mock_org_query
        mock_org_query.update.side_effect = Exception("Org update failed")

        email_updated, phone_updated = await _update_email_or_phone(
            "user-123",
            input_phone,
            VerificationTrigger.PHONE_NUMBER_UPDATE.value,
            "fake-token"
        )
        
        assert email_updated is False
        assert phone_updated is True
        
        assert mock_admin_api.update_user_by_id.call_count >= 4

@pytest.mark.asyncio
async def test_update_email_metadata_verification_mismatch():
    """
    Test email update where metadata verification reveals mismatch, triggering specific retry branch.
    """
    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()
    
    mock_admin_client = MagicMock()
    mock_admin_auth = MagicMock()
    mock_admin_api = MagicMock()
    mock_admin_client.auth = mock_admin_auth
    mock_admin_auth.admin = mock_admin_api
    
    target_email = "new@example.com"
    
    # Setup verification to fail metadata check
    verify_user_response = MagicMock()
    verify_user_response.user.user_metadata = {"email": "WRONG@example.com"}
    
    with patch('apps.user_service.app.api.verification_codes._get_supabase_client_with_token',
               AsyncMock(return_value=mock_client)), \
         patch.object(mock_client.auth, 'get_user', AsyncMock(return_value=mock_user_response)), \
         patch('apps.user_service.app.api.verification_codes.os.getenv', return_value="secret"), \
         patch('apps.user_service.app.api.verification_codes.jwt.decode', return_value={"exp": time.time() + 3600}), \
         patch('libs.shared_db.supabase_db.db.get_supabase_admin_client',
               AsyncMock(return_value=mock_admin_client)), \
         patch('supabase_auth.types.Session'), \
         patch('supabase_auth.types.User'), \
         patch('supabase_auth.helpers.model_dump_json', return_value="{}"):
            
        # Session persistence setup - Critical for fixing the TypeError
        mock_client.auth._persist_session = True
        mock_client.auth._storage.set_item = AsyncMock()

        mock_admin_api.get_user_by_id = AsyncMock(side_effect=[
            MagicMock(user=MagicMock(user_metadata={})), # Initial
            verify_user_response, # Verification check fails
            MagicMock(user=MagicMock(email=target_email)) # Final check
        ])
        
        mock_admin_api.update_user_by_id = AsyncMock(return_value=MagicMock(user=MagicMock(email=target_email)))

        # Mock org update to return no data (line 715 coverage)
        mock_org_execute = AsyncMock(return_value=MagicMock(data=[]))
        mock_admin_client.table.return_value.update.return_value.eq.return_value.execute = mock_org_execute

        await _update_email_or_phone(
            "user-123",
            target_email,
            VerificationTrigger.EMAIL_UPDATE.value,
            "fake-token"
        )
        
        assert mock_admin_api.update_user_by_id.call_count >= 3

@pytest.mark.asyncio
async def test_session_persistence_error():
    """Test error handling when saving session to storage fails."""
    mock_client = MagicMock()
    mock_user_response = MagicMock()
    mock_user_response.user = MagicMock()
    
    with patch('apps.user_service.app.api.verification_codes._get_supabase_client_with_token',
               AsyncMock(return_value=mock_client)), \
         patch.object(mock_client.auth, 'get_user', AsyncMock(return_value=mock_user_response)), \
         patch('apps.user_service.app.api.verification_codes.os.getenv', return_value="secret"), \
         patch('apps.user_service.app.api.verification_codes.jwt.decode', return_value={"exp": time.time() + 3600}), \
         patch('supabase_auth.types.Session'), \
         patch('supabase_auth.types.User'), \
         patch('supabase_auth.helpers.model_dump_json', return_value="{}"):
        
        mock_client.auth._persist_session = True
        # This simulates the error in storage
        mock_client.auth._storage.set_item = AsyncMock(side_effect=Exception("Storage failed"))
        
        with pytest.raises(HTTPException) as exc:
            await _update_email_or_phone(
                "user-123",
                "test@example.com",
                VerificationTrigger.EMAIL_UPDATE.value,
                "fake-token"
            )
        assert exc.value.status_code == 500
        assert "Failed to create session" in exc.value.detail

