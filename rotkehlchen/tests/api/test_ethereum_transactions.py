import os
import random
from contextlib import ExitStack
from http import HTTPStatus
from typing import TYPE_CHECKING, Optional
from unittest.mock import patch

import gevent
import pytest
import requests

from rotkehlchen.accounting.structures.evm_event import EvmProduct
from rotkehlchen.accounting.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.chain.ethereum.modules.curve.constants import CPT_CURVE
from rotkehlchen.chain.ethereum.transactions import EthereumTransactions
from rotkehlchen.chain.evm.decoding.constants import CPT_GAS
from rotkehlchen.chain.evm.structures import EvmTxReceipt
from rotkehlchen.constants import ONE
from rotkehlchen.constants.assets import A_BTC, A_DAI, A_ETH, A_MKR, A_USDT, A_WETH
from rotkehlchen.constants.limits import FREE_ETH_TX_LIMIT, FREE_HISTORY_EVENTS_LIMIT
from rotkehlchen.db.evmtx import DBEvmTx
from rotkehlchen.db.filtering import EvmEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.db.ranges import DBQueryRanges
from rotkehlchen.externalapis.etherscan import Etherscan
from rotkehlchen.fval import FVal
from rotkehlchen.tests.utils.api import (
    api_url_for,
    assert_error_response,
    assert_ok_async_response,
    assert_proper_response,
    assert_proper_response_with_result,
    assert_simple_ok_response,
    wait_for_async_task,
)
from rotkehlchen.tests.utils.checks import assert_serialized_lists_equal
from rotkehlchen.tests.utils.constants import TXHASH_HEX_TO_BYTES
from rotkehlchen.tests.utils.ethereum import (
    TEST_ADDR1,
    TEST_ADDR2,
    TEST_ADDR3,
    extended_transactions_setup_test,
    setup_ethereum_transactions_test,
)
from rotkehlchen.tests.utils.factories import (
    generate_events_response,
    make_ethereum_event,
    make_ethereum_transaction,
    make_evm_address,
)
from rotkehlchen.tests.utils.mock import MockResponse, mock_evm_chains_with_transactions
from rotkehlchen.tests.utils.rotkehlchen import setup_balances
from rotkehlchen.types import (
    ChainID,
    ChecksumEvmAddress,
    EvmTransaction,
    EVMTxHash,
    SupportedBlockchain,
    Timestamp,
    TimestampMS,
    deserialize_evm_tx_hash,
)
from rotkehlchen.utils.hexbytes import hexstring_to_bytes

if TYPE_CHECKING:
    from rotkehlchen.api.server import APIServer
    from rotkehlchen.db.dbhandler import DBHandler

EXPECTED_AFB7_TXS = [{
    'tx_hash': '0x13684203a4bf07aaed0112983cb380db6004acac772af2a5d46cb2a28245fbad',
    'evm_chain': 'ethereum',
    'timestamp': 1439984408,
    'block_number': 111083,
    'from_address': '0xC47Aaa860008be6f65B58c6C6E02a84e666EfE31',
    'to_address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    'value': '37451082560000003241',
    'gas': '90000',
    'gas_price': '58471444665',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 100,
}, {
    'tx_hash': '0xe58af420fd8430c061303e4c5bd2668fafbc0fd41078fa6aa01d7781c1dadc7a',
    'evm_chain': 'ethereum',
    'timestamp': 1461221228,
    'block_number': 1375816,
    'from_address': '0x9e6316f44BaEeeE5d41A1070516cc5fA47BAF227',
    'to_address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    'value': '389359660000000000',
    'gas': '250000',
    'gas_price': '20000000000',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 326,
}, {
    'tx_hash': '0x0ae8b470b4a69c7f6905b9ec09f50c8772821080d11ba0acc83ac23a7ccb4ad8',
    'evm_chain': 'ethereum',
    'timestamp': 1461399856,
    'block_number': 1388248,
    'from_address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    'to_address': '0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2',
    'value': '37840020860000003241',
    'gas': '21068',
    'gas_price': '20000000000',
    'gas_used': '21068',
    'input_data': '0x01',
    'nonce': 0,
}, {
    'tx_hash': '0x2f6f167e32e9cb1bef40b92e831c3f1d1cd0348bb72dcc723bde94f51944ebd6',
    'evm_chain': 'ethereum',
    'timestamp': 1494458609,
    'block_number': 3685519,
    'from_address': '0x4aD11d04CCd80A83d48096478b73D1E8e0ed49D6',
    'to_address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    'value': '6000000000000000000',
    'gas': '21000',
    'gas_price': '21000000000',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 1,
}, {
    'tx_hash': '0x5d81f937ad37349f89dc6e9926988855bb6c6e1e00c683ee3b7cb7d7b09b5567',
    'evm_chain': 'ethereum',
    'timestamp': 1494458861,
    'block_number': 3685532,
    'from_address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    'to_address': '0xFa52274DD61E1643d2205169732f29114BC240b3',
    'value': '5999300000000000000',
    'gas': '35000',
    'gas_price': '20000000000',
    'gas_used': '30981',
    'input_data': '0xf7654176',
    'nonce': 1,
}]

EXPECTED_4193_TXS = [{
    'tx_hash': '0x2964f3a91408337b05aeb8f8f670f4107999be05376e630742404664c96a5c31',
    'evm_chain': 'ethereum',
    'timestamp': 1439979000,
    'block_number': 110763,
    'from_address': '0x976349705b839e2F5719387Fb27D2519d519da03',
    'to_address': '0x4193122032b38236825BBa166F42e54fc3F4A1EE',
    'value': '100000000000000000',
    'gas': '90000',
    'gas_price': '57080649960',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 30,
}, {
    'tx_hash': '0xb99a6e0b40f38c4887617bc1df560fde1d0456b712cb2bb1b52fdb8880d3cd74',
    'evm_chain': 'ethereum',
    'timestamp': 1439984825,
    'block_number': 111111,
    'from_address': '0x4193122032b38236825BBa166F42e54fc3F4A1EE',
    'to_address': '0x1177848589133f5C4E69EdFcb18bBCd9cACE72D1',
    'value': '20000000000000000',
    'gas': '90000',
    'gas_price': '59819612547',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 0,
}, {
    'tx_hash': '0xfadf1f12281ee2c0311055848b4ffc0046ac80afae4a9d3640b5f57bb8a7795a',
    'evm_chain': 'ethereum',
    'timestamp': 1507291254,
    'block_number': 4341870,
    'from_address': '0x4193122032b38236825BBa166F42e54fc3F4A1EE',
    'to_address': '0x2B06E2ea21e184589853225888C93b9b8e0642f6',
    'value': '78722788136513000',
    'gas': '21000',
    'gas_price': '1000000000',
    'gas_used': '21000',
    'input_data': '0x',
    'nonce': 1,
}]


def query_events(server, json, expected_num_with_grouping, expected_totals_with_grouping, entries_limit=-1):  # noqa: E501
    """Query history events as frontend would have, with grouped identifiers

    First query all events with grouping enabled. Then if any events have more,
    take those events and ask for the extras. Return the full set.
    """
    extra_json = json.copy() | {'group_by_event_ids': True}
    response = requests.post(
        api_url_for(server, 'historyeventresource'),
        json=extra_json,
    )
    result = assert_proper_response_with_result(response)
    entries = result['entries']
    assert result['entries_limit'] == entries_limit
    assert result['entries_found'] == expected_num_with_grouping
    assert result['entries_total'] == expected_totals_with_grouping
    assert len(entries) == expected_num_with_grouping

    augmented_entries = []
    for entry in entries:
        if entry['grouped_events_num'] != 1:
            extra_json = json.copy() | {'event_identifiers': [entry['entry']['event_identifier']]}
            response = requests.post(
                api_url_for(server, 'historyeventresource'),
                json=extra_json,
            )
            result = assert_proper_response_with_result(response)
            augmented_entries.extend(result['entries'])
        else:
            entry.pop('grouped_events_num')
            augmented_entries.append(entry)

    return augmented_entries


def assert_force_redecode_txns_works(api_server: 'APIServer', hashes: Optional[list[EVMTxHash]]):
    rotki = api_server.rest_api.rotkehlchen
    get_eth_txns_patch = patch.object(
        rotki.chains_aggregator.ethereum.transactions_decoder.transactions,
        'get_or_create_transaction',
        wraps=rotki.chains_aggregator.ethereum.transactions_decoder.transactions.get_or_create_transaction,
    )
    get_or_decode_txn_events_patch = patch.object(
        rotki.chains_aggregator.ethereum.transactions_decoder,
        '_get_or_decode_transaction_events',
        wraps=rotki.chains_aggregator.ethereum.transactions_decoder._get_or_decode_transaction_events,
    )
    with ExitStack() as stack:
        function_call_counters = []
        function_call_counters.append(stack.enter_context(get_or_decode_txn_events_patch))
        function_call_counters.append(stack.enter_context(get_eth_txns_patch))

        response = requests.put(
            api_url_for(
                api_server,
                'evmtransactionsresource',
            ), json={
                'async_query': False,
                'ignore_cache': True,
                'data': [{
                    'tx_hashes': hashes,
                    'evm_chain': 'ethereum',
                }],
            },
        )
        assert_proper_response(response)
        if hashes is None:
            for fn in function_call_counters:
                assert fn.call_count == 14
        else:
            txn_hashes_len = len(hashes)
            for fn in function_call_counters:
                assert fn.call_count == txn_hashes_len


def _write_transactions_to_db(
        db: 'DBHandler',
        transactions: list[EvmTransaction],
        extra_transactions: list[EvmTransaction],
        ethereum_accounts: list[ChecksumEvmAddress],
        start_ts: Timestamp,
        end_ts: Timestamp,
) -> None:
    """Common function to replicate writing transactions in the DB for tests in this file"""
    with db.user_write() as cursor:
        dbevmtx = DBEvmTx(db)
        dbevmtx.add_evm_transactions(cursor, transactions, relevant_address=ethereum_accounts[0])
        dbevmtx.add_evm_transactions(cursor, extra_transactions, relevant_address=ethereum_accounts[1])  # noqa: E501
        # Also make sure to update query ranges so as not to query etherscan at all
        for address in ethereum_accounts:
            for prefix in (SupportedBlockchain.ETHEREUM.to_range_prefix('txs'), SupportedBlockchain.ETHEREUM.to_range_prefix('internaltxs'), SupportedBlockchain.ETHEREUM.to_range_prefix('tokentxs')):  # noqa: E501
                DBQueryRanges(db).update_used_query_range(
                    write_cursor=cursor,
                    location_string=f'{prefix}_{address}',
                    queried_ranges=[(start_ts, end_ts)],
                )


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('ethereum_accounts', [[
    '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7',
    '0x4193122032b38236825BBa166F42e54fc3F4A1EE',
]])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [FVal(1.5)])
def test_query_transactions(rotkehlchen_api_server):
    """Test that querying the ethereum transactions endpoint works as expected.
    Also tests that requesting for transaction decoding works.

    This test uses real data.
    """
    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    # Check that we get all transactions
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={'async_query': async_query, 'evm_chain': 'ethereum'},
    )
    if async_query:
        task_id = assert_ok_async_response(response)
        outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
        assert outcome['message'] == ''
        result = outcome['result']
    else:
        result = assert_proper_response_with_result(response)
    expected_result = EXPECTED_AFB7_TXS + EXPECTED_4193_TXS
    expected_result.sort(key=lambda x: x['timestamp'])
    expected_result.reverse()

    # Make sure that all of the transactions we expect are there and in order
    # There can be more transactions (since the address can make more)
    # but this check ignores them
    previous_index = 0
    result_entries = [x['entry'] for x in result['entries']]
    for entry in expected_result:
        assert entry in result_entries
        entry_idx = result_entries.index(entry)
        if previous_index != 0:
            assert entry_idx == previous_index + 1
        previous_index = entry_idx

    assert result['entries_found'] >= len(expected_result)
    assert result['entries_limit'] == FREE_ETH_TX_LIMIT

    # now let's ignore two transactions
    ignored_data = [f"1{EXPECTED_AFB7_TXS[2]['tx_hash']}", f"1{EXPECTED_AFB7_TXS[3]['tx_hash']}"]
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'ignoredactionsresource',
        ), json={'action_type': 'history_event', 'data': ignored_data},
    )
    assert_simple_ok_response(response)

    # Check that transactions per address and in a specific time range can be
    # queried and that this is from the DB and not etherscan
    def mock_etherscan_get(url, *args, **kwargs):  # pylint: disable=unused-argument
        return MockResponse(200, '{}')
    etherscan_patch = patch.object(rotki.chains_aggregator.ethereum.node_inquirer.etherscan.session, 'get', wraps=mock_etherscan_get)  # noqa: E501
    with etherscan_patch as mock_call:
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ), json={
                'async_query': async_query,
                'from_timestamp': 1461399856,
                'to_timestamp': 1494458860,
                'evm_chain': 'ethereum',
                'accounts': [{'address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7'}],
            },
        )
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
            assert outcome['message'] == ''
            result = outcome['result']
        else:
            result = assert_proper_response_with_result(response)

        assert mock_call.call_count == 0

    result_entries = [x['entry'] for x in result['entries']]
    assert result_entries == EXPECTED_AFB7_TXS[2:4][::-1]

    # Also check that requesting decoding of tx_hashes gets receipts and decodes events
    hashes = [EXPECTED_AFB7_TXS[0]['tx_hash'], EXPECTED_4193_TXS[0]['tx_hash']]
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': async_query,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': hashes,
            }],
        },
    )
    if async_query:
        task_id = assert_ok_async_response(response)
        outcome = wait_for_async_task(rotkehlchen_api_server, task_id)
        assert outcome['message'] == ''
        result = outcome['result']
    else:
        result = assert_proper_response_with_result(response)
    assert result is True

    dbevmtx = DBEvmTx(rotki.data.db)
    dbevents = DBHistoryEvents(rotki.data.db)
    event_ids = set()
    with rotki.data.db.conn.read_ctx() as cursor:
        for tx_hash_hex in hashes:
            receipt = dbevmtx.get_receipt(cursor, hexstring_to_bytes(tx_hash_hex), ChainID.ETHEREUM)  # noqa: E501
            assert isinstance(receipt, EvmTxReceipt) and receipt.tx_hash == hexstring_to_bytes(tx_hash_hex)  # noqa: E501
            events = dbevents.get_history_events(
                cursor=cursor,
                filter_query=EvmEventFilterQuery.make(
                    tx_hashes=[TXHASH_HEX_TO_BYTES[tx_hash_hex]],
                ),
                has_premium=True,  # for this function we don't limit. We only limit txs.
            )
            event_ids.add(events[0].identifier)
            assert len(events) == 1
            assert events[0].balance.usd_value == events[0].balance.amount * FVal(1.5)

    # see that if same transaction hash is requested for decoding events are not re-decoded
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': hashes,
            }],
        },
    )

    with rotki.data.db.conn.read_ctx() as cursor:
        result = assert_proper_response_with_result(response)
        for tx_hash_hex in hashes:
            receipt = dbevmtx.get_receipt(cursor, hexstring_to_bytes(tx_hash_hex), ChainID.ETHEREUM)  # noqa: E501
            assert isinstance(receipt, EvmTxReceipt) and receipt.tx_hash == hexstring_to_bytes(tx_hash_hex)  # noqa: E501
            events = dbevents.get_history_events(
                cursor=cursor,
                filter_query=EvmEventFilterQuery.make(
                    tx_hashes=[TXHASH_HEX_TO_BYTES[tx_hash_hex]],
                ),
                has_premium=True,  # for this function we don't limit. We only limit txs.
            )
            assert len(events) == 1
            assert events[0].identifier in event_ids

    # Check that force re-requesting the events works
    assert_force_redecode_txns_works(rotkehlchen_api_server, hashes)
    # check that passing no transaction hashes, decodes all transaction
    assert_force_redecode_txns_works(rotkehlchen_api_server, None)

    # see that empty list of hashes to decode is an error
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={'async_query': False, 'data': [{'evm_chain': 'ethereum', 'tx_hashes': []}]},
    )
    assert_error_response(
        response=response,
        contained_in_msg='Empty list of hashes is a noop. Did you mean to omit the list?',
        status_code=HTTPStatus.BAD_REQUEST,
    )


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_request_transaction_decoding_errors(rotkehlchen_api_server):
    """Test that the request transaction decoding endpoint handles input errors"""
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': [1, '0xfc4f300f4d9e6436825ed0dc85716df4648a64a29570280c6e6261acf041aa4b'],  # noqa: E501
            }],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Transaction hash should be a string',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': ['dasd', '0xfc4f300f4d9e6436825ed0dc85716df4648a64a29570280c6e6261acf041aa4b'],  # noqa: E501
            }],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Could not turn transaction hash dasd to bytes',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': ['0x34af01', '0xfc4f300f4d9e6436825ed0dc85716df4648a64a29570280c6e6261acf041aa4b'],  # noqa: E501
            }],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Transaction hashes should be 32 bytes in length',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    nonexisting_hash = '0x1c4f300f4d9e6436825ed0dc85716df4648a64a29570280c6e6261acf041aa41'
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': [nonexisting_hash],
            }],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg=f'hash {nonexisting_hash} does not correspond to a transaction',
        status_code=HTTPStatus.CONFLICT,
    )

    # trying to get transactions for a chaind that doesn't support yet them
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'evm_chain': 'avalanche',
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='rotki does not support evm transactions for avalanche',
        status_code=HTTPStatus.BAD_REQUEST,
    )


@pytest.mark.skipif(
    'CI' in os.environ,
    reason='SLOW TEST -- run locally from time to time',
)
@pytest.mark.parametrize('ethereum_accounts', [['0xe62193Bc1c340EF2205C0Bd71691Fad5e5072253']])
@pytest.mark.parametrize('start_with_valid_premium', [True])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_query_over_10k_transactions(rotkehlchen_api_server):
    """Test that querying for an address with over 10k transactions works

    This test uses real etherscan queries and an address that we found that has > 10k transactions.

    Etherscan has a limit for 1k transactions per query and we need to make
    sure that we properly pull all data by using pagination
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    original_get = requests.get

    def mock_some_etherscan_queries(etherscan: Etherscan):
        """Just hit etherscan for the actual transations and mock all else.
        This test just needs to see that pagination works on the tx endpoint
        """
        def mocked_request_dict(url, *_args, **_kwargs):
            if '=txlistinternal&' in url or '=tokentx&' in url:
                # don't return any internal or token transactions
                payload = '{"status":"1","message":"OK","result":[]}'
            elif '=getblocknobytime&' in url or '=txlist&' in url:
                # we don't really care about this in this test so return original
                return original_get(url)
            else:
                raise AssertionError(f'Unexpected etherscan query {url} at test mock')
            return MockResponse(200, payload)

        return patch.object(etherscan.session, 'get', wraps=mocked_request_dict)

    expected_at_least = 16097  # 30/08/2020
    with mock_some_etherscan_queries(rotki.chains_aggregator.ethereum.node_inquirer.etherscan):
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ),
            json={'evm_chain': 'ethereum'},
        )

    result = assert_proper_response_with_result(response)
    assert len(result['entries']) >= expected_at_least
    assert result['entries_found'] >= expected_at_least
    assert result['entries_limit'] == -1

    # Also check some entries in the list that we know of to see that they exist
    rresult = [x['entry'] for x in result['entries'][::-1]]

    assert rresult[1]['tx_hash'] == '0xec72748b8b784380ff6fcca9b897d649a0992eaa63b6c025ecbec885f64d2ac9'  # noqa: E501
    assert rresult[1]['nonce'] == 0
    assert rresult[11201]['tx_hash'] == '0x118edf91d6d47fcc6bc9c7ceefe2ee2344e0ff3b5a1805a804fa9c9448efb746'  # noqa: E501
    assert rresult[11201]['nonce'] == 11198
    assert rresult[16172]['tx_hash'] == '0x92baec6dbf3351a1aea2371453bfcb5af898ffc8172fcf9577ca2e5335df4c71'  # noqa: E501
    assert rresult[16172]['nonce'] == 16169


def test_query_transactions_errors(rotkehlchen_api_server):
    # Malformed address
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={'accounts': [{'address': '0xasdasd'}]},
    )
    assert_error_response(
        response=response,
        contained_in_msg='address": ["Given value 0xasdasd is not an ethereum address',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Malformed from_timestamp
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ),
        json={
            'accounts': [{'address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7'}],
            'from_timestamp': 'foo',
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Failed to deserialize a timestamp entry from string foo',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Malformed to_timestamp
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ),
        json={
            'accounts': [{'address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7'}],
            'to_timestamp': 'foo',
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Failed to deserialize a timestamp entry from string foo',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid order_by_attribute
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ),
        json={
            'accounts': [{'address': '0xaFB7ed3beBE50E0b62Fa862FAba93e7A46e59cA7'}],
            'order_by_attributes': ['tim3'],
            'ascending': [False],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='order_by_attributes for transactions can not be tim3',
        status_code=HTTPStatus.BAD_REQUEST,
    )


@pytest.mark.parametrize('start_with_valid_premium', [False, True])
@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_query_transactions_over_limit(
        rotkehlchen_api_server,
        ethereum_accounts,
        start_with_valid_premium,
):
    start_ts = 0
    end_ts = 1598453214
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    db = rotki.data.db
    all_transactions_num = FREE_ETH_TX_LIMIT + 50
    transactions = [EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(x.to_bytes(2, byteorder='little')),
        chain_id=ChainID.ETHEREUM,
        timestamp=x,
        block_number=x,
        from_address=ethereum_accounts[0],
        to_address=make_evm_address(),
        value=x,
        gas=x,
        gas_price=x,
        gas_used=x,
        input_data=x.to_bytes(2, byteorder='little'),
        nonce=x,
    ) for x in range(FREE_ETH_TX_LIMIT - 10)]
    extra_transactions = [EvmTransaction(
        tx_hash=deserialize_evm_tx_hash((x + 500).to_bytes(2, byteorder='little')),
        chain_id=ChainID.ETHEREUM,
        timestamp=x,
        block_number=x,
        from_address=ethereum_accounts[1],
        to_address=make_evm_address(),
        value=x,
        gas=x,
        gas_price=x,
        gas_used=x,
        input_data=x.to_bytes(2, byteorder='little'),
        nonce=x,
    ) for x in range(60)]

    _write_transactions_to_db(db=db, transactions=transactions, extra_transactions=extra_transactions, ethereum_accounts=ethereum_accounts, start_ts=start_ts, end_ts=end_ts)  # noqa: E501

    free_expected_entries_total = [FREE_ETH_TX_LIMIT - 35, 35]
    free_expected_entries_found = [FREE_ETH_TX_LIMIT - 10, 60]
    premium_expected_entries = [FREE_ETH_TX_LIMIT - 10, 60]

    # Check that we get all transactions correctly even if we query two times
    for _ in range(2):
        for idx, address in enumerate(ethereum_accounts):
            response = requests.post(
                api_url_for(
                    rotkehlchen_api_server,
                    'evmtransactionsresource',
                ), json={
                    'evm_chain': 'ethereum',
                    'from_timestamp': start_ts,
                    'to_timestamp': end_ts,
                    'accounts': [{'address': address}],
                },
            )
            result = assert_proper_response_with_result(response)
            if start_with_valid_premium:
                assert len(result['entries']) == premium_expected_entries[idx]
                assert result['entries_total'] == all_transactions_num
                assert result['entries_found'] == premium_expected_entries[idx]
                assert result['entries_limit'] == -1
            else:
                assert len(result['entries']) == free_expected_entries_total[idx]
                assert result['entries_total'] == all_transactions_num
                assert result['entries_found'] == free_expected_entries_found[idx]
                assert result['entries_limit'] == FREE_ETH_TX_LIMIT


@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_query_transactions_from_to_address(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Make sure that if a transaction is just being sent to an address it's also returned."""
    start_ts = 0
    end_ts = 1598453214
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    db = rotki.data.db
    transactions = [EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(b'1'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[0],
        to_address=make_evm_address(),
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=0,
    ), EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(b'2'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[0],
        to_address=ethereum_accounts[1],
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=1,
    ), EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(b'3'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=make_evm_address(),
        to_address=ethereum_accounts[0],
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=55,
    )]

    _write_transactions_to_db(db=db, transactions=transactions, extra_transactions=[transactions[1]], ethereum_accounts=ethereum_accounts, start_ts=start_ts, end_ts=end_ts)  # noqa: E501

    expected_entries = {ethereum_accounts[0]: 3, ethereum_accounts[1]: 1}
    # Check that we get all transactions correctly even if we query two times
    for _ in range(2):
        for address in ethereum_accounts:
            response = requests.post(
                api_url_for(
                    rotkehlchen_api_server,
                    'evmtransactionsresource',
                ), json={
                    'evm_chain': 'ethereum',
                    'from_timestamp': start_ts,
                    'to_timestamp': end_ts,
                    'accounts': [{'address': address}],
                },
            )
            result = assert_proper_response_with_result(response)
            assert len(result['entries']) == expected_entries[address]
            assert result['entries_limit'] == FREE_ETH_TX_LIMIT
            assert result['entries_found'] == expected_entries[address]
            assert result['entries_total'] == 3


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_query_transactions_removed_address(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Make sure that if an address is removed so are the transactions from the DB.
    Also assure that a transaction is not deleted so long as it touches a tracked
    address, even if one of the touched address is removed.
    """
    start_ts = 0
    end_ts = 1598453214
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    db = rotki.data.db
    transactions = [EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(b'1'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[0],
        to_address=make_evm_address(),
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=0,
    ), EvmTransaction(
        tx_hash=deserialize_evm_tx_hash(b'2'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[0],
        to_address=make_evm_address(),
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=1,
    ), EvmTransaction(  # should remain after deleting account[0]
        tx_hash=deserialize_evm_tx_hash(b'3'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=make_evm_address(),
        to_address=ethereum_accounts[1],
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=55,
    ), EvmTransaction(  # should remain after deleting account[0]
        tx_hash=deserialize_evm_tx_hash(b'4'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[1],
        to_address=ethereum_accounts[0],
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=0,
    ), EvmTransaction(  # should remain after deleting account[0]
        tx_hash=deserialize_evm_tx_hash(b'5'),
        chain_id=ChainID.ETHEREUM,
        timestamp=0,
        block_number=0,
        from_address=ethereum_accounts[0],
        to_address=ethereum_accounts[1],
        value=1,
        gas=1,
        gas_price=1,
        gas_used=1,
        input_data=b'',
        nonce=0,
    )]

    _write_transactions_to_db(db=db, transactions=transactions[0:2] + transactions[3:], extra_transactions=transactions[2:], ethereum_accounts=ethereum_accounts, start_ts=start_ts, end_ts=end_ts)  # noqa: E501

    # Now remove the first account (do the mocking to not query etherscan for balances)
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=[],
        eth_balances=['10000', '10000'],
    )
    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.delete(api_url_for(
            rotkehlchen_api_server,
            'blockchainsaccountsresource',
            blockchain='ETH',
        ), json={'accounts': [ethereum_accounts[0]]})
    assert_proper_response_with_result(response)

    # Check that only the 3 remaining transactions from the other account are returned
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ),
        json={'evm_chain': 'ethereum'},
    )
    result = assert_proper_response_with_result(response)
    assert len(result['entries']) == 3
    assert result['entries_found'] == 3


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_transaction_same_hash_same_nonce_two_tracked_accounts(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Make sure that if we track two addresses and they send one transaction
    to each other it's not counted as duplicate in the DB but is returned
    every time by both addresses"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    def mock_etherscan_transaction_response(etherscan: Etherscan, eth_accounts):
        def mocked_request_dict(url, *_args, **_kwargs):

            addr1_tx = f"""{{"blockNumber":"1","timeStamp":"1","hash":"0x9c81f44c29ff0226f835cd0a8a2f2a7eca6db52a711f8211b566fd15d3e0e8d4","nonce":"0","blockHash":"0xd3cabad6adab0b52ea632c386ea19403680571e682c62cb589b5abcd76de2159","transactionIndex":"0","from":"{eth_accounts[0]}","to":"{eth_accounts[1]}","value":"1","gas":"2000000","gasPrice":"10000000000000","isError":"0","txreceipt_status":"","input":"0x","contractAddress":"","cumulativeGasUsed":"1436963","gasUsed":"1436963","confirmations":"1"}}"""  # noqa: E501
            addr2_txs = f"""{addr1_tx}, {{"blockNumber":"2","timeStamp":"2","hash":"0x1c81f54c29ff0226f835cd0a2a2f2a7eca6db52a711f8211b566fd15d3e0e8d4","nonce":"1","blockHash":"0xd1cabad2adab0b56ea632c386ea19403680571e682c62cb589b5abcd76de2159","transactionIndex":"0","from":"{eth_accounts[1]}","to":"{make_evm_address()}","value":"1","gas":"2000000","gasPrice":"10000000000000","isError":"0","txreceipt_status":"","input":"0x","contractAddress":"","cumulativeGasUsed":"1436963","gasUsed":"1436963","confirmations":"1"}}"""  # noqa: E501
            if '=txlistinternal&' in url or 'action=tokentx&' in url:
                # don't return any internal or token transactions
                payload = '{"status":"1","message":"OK","result":[]}'
            elif '=txlist&' in url:
                if eth_accounts[0] in url:
                    tx_str = addr1_tx
                elif eth_accounts[1] in url:
                    tx_str = addr2_txs
                else:
                    raise AssertionError(
                        'Requested etherscan transactions for unknown address in tests',
                    )
                payload = f'{{"status":"1","message":"OK","result":[{tx_str}]}}'
            elif '=getblocknobytime&' in url:
                # we don't really care about this so just return whatever
                payload = '{"status":"1","message":"OK","result": "1"}'
            else:
                raise AssertionError('Got in unexpected section during test')

            return MockResponse(200, payload)

        return patch.object(etherscan.session, 'get', wraps=mocked_request_dict)

    with mock_etherscan_transaction_response(rotki.chains_aggregator.ethereum.node_inquirer.etherscan, ethereum_accounts):  # noqa: E501
        # Check that we get transaction both when we query all accounts and each one individually
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ),
            json={'evm_chain': 'ethereum'},
        )
        result = assert_proper_response_with_result(response)
        assert len(result['entries']) == 2
        assert result['entries_found'] == 2
        assert result['entries_total'] == 2

        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ),
            json={
                'evm_chain': 'ethereum',
                'accounts': [{'address': ethereum_accounts[0]}],
            },
        )
        result = assert_proper_response_with_result(response)
        assert len(result['entries']) == 1
        assert result['entries_found'] == 1
        assert result['entries_total'] == 2
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ),
            json={
                'evm_chain': 'ethereum',
                'accounts': [{'address': ethereum_accounts[1], 'evm_chain': 'ethereum'}],
            },
        )
        result = assert_proper_response_with_result(response)
        assert len(result['entries']) == 2
        assert result['entries_found'] == 2
        assert result['entries_total'] == 2


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('ethereum_accounts', [['0x6e15887E2CEC81434C16D587709f64603b39b545']])
@pytest.mark.parametrize('start_with_valid_premium', [True])
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_query_transactions_check_decoded_events(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Test that transactions and associated events can be queried via their respective endpoints.

    Also test that if an event is edited or added to a transaction that transaction and
    event are not purged when the ethereum transactions are purged. And if transactions
    are requeried the edited events are there.
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    start_ts = Timestamp(0)
    end_ts = Timestamp(1642803566)  # time of test writing
    dbevents = DBHistoryEvents(rotki.data.db)

    def query_transactions(rotki):
        rotki.chains_aggregator.ethereum.transactions.single_address_query_transactions(
            address=ethereum_accounts[0],
            start_ts=start_ts,
            end_ts=end_ts,
        )
        with mock_evm_chains_with_transactions():
            rotki.task_manager._maybe_schedule_evm_txreceipts()
            gevent.joinall(rotki.greenlet_manager.greenlets)
            rotki.task_manager._maybe_decode_evm_transactions()
            gevent.joinall(rotki.greenlet_manager.greenlets)
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'evmtransactionsresource',
            ),
            json={
                'evm_chain': 'ethereum',
                'from_timestamp': start_ts,
                'to_timestamp': end_ts,
            },
        )
        return assert_proper_response_with_result(response)

    tx_result = query_transactions(rotki)
    assert len(tx_result['entries']) == 4
    returned_events = query_events(rotkehlchen_api_server, json={'location': 'ethereum'}, expected_num_with_grouping=4, expected_totals_with_grouping=4)  # noqa: E501

    tx1_events = [{
        'entry': {
            'identifier': 4,
            'entry_type': 'evm event',
            'asset': 'ETH',
            'balance': {'amount': '0.00863351371344', 'usd_value': '0'},
            'counterparty': CPT_GAS,
            'address': None,
            'event_identifier': '10x8d822b87407698dd869e830699782291155d0276c5a7e5179cb173608554e41f',  # noqa: E501
            'event_subtype': 'fee',
            'event_type': 'spend',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Burned 0.00863351371344 ETH for gas',
            'product': None,
            'sequence_index': 0,
            'timestamp': 1642802807000,
            'tx_hash': '0x8d822b87407698dd869e830699782291155d0276c5a7e5179cb173608554e41f',
            'extra_data': None,
        },
    }, {
        'entry': {
            'identifier': 5,
            'entry_type': 'evm event',
            'asset': 'ETH',
            'balance': {'amount': '0.096809163374771208', 'usd_value': '0'},
            'counterparty': None,
            'address': '0xA090e606E30bD747d4E6245a1517EbE430F0057e',
            'event_identifier': '10x8d822b87407698dd869e830699782291155d0276c5a7e5179cb173608554e41f',  # noqa: E501
            'event_subtype': 'none',
            'event_type': 'spend',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Send 0.096809163374771208 ETH to 0xA090e606E30bD747d4E6245a1517EbE430F0057e',
            'product': None,
            'sequence_index': 1,
            'timestamp': 1642802807000,
            'tx_hash': '0x8d822b87407698dd869e830699782291155d0276c5a7e5179cb173608554e41f',
            'extra_data': None,
        },
    }]
    assert returned_events[:2] == tx1_events
    tx2_events = [{
        'entry': {
            'identifier': 1,
            'entry_type': 'evm event',
            'asset': 'ETH',
            'address': None,
            'balance': {'amount': '0.017690836625228792', 'usd_value': '0'},
            'counterparty': CPT_GAS,
            'event_identifier': '10x38ed9c2d4f0855f2d88823d502f8794b993d28741da48724b7dfb559de520602',  # noqa: E501
            'event_subtype': 'fee',
            'event_type': 'spend',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Burned 0.017690836625228792 ETH for gas',
            'product': None,
            'sequence_index': 0,
            'timestamp': 1642802735000,
            'tx_hash': '0x38ed9c2d4f0855f2d88823d502f8794b993d28741da48724b7dfb559de520602',
            'extra_data': None,
        },
    }, {
        'entry': {
            'identifier': 2,
            'entry_type': 'evm event',
            'asset': A_USDT.identifier,
            'address': '0xb5d85CBf7cB3EE0D56b3bB207D5Fc4B82f43F511',
            'balance': {'amount': '1166', 'usd_value': '0'},
            'counterparty': None,
            'event_identifier': '10x38ed9c2d4f0855f2d88823d502f8794b993d28741da48724b7dfb559de520602',  # noqa: E501
            'event_subtype': 'none',
            'event_type': 'spend',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Send 1166 USDT from 0x6e15887E2CEC81434C16D587709f64603b39b545 to 0xb5d85CBf7cB3EE0D56b3bB207D5Fc4B82f43F511',  # noqa: E501
            'product': None,
            'sequence_index': 308,
            'timestamp': 1642802735000,
            'tx_hash': '0x38ed9c2d4f0855f2d88823d502f8794b993d28741da48724b7dfb559de520602',
            'extra_data': None,
        },
    }]
    assert returned_events[2:4] == tx2_events
    tx3_events = [{
        'entry': {
            'identifier': 3,
            'entry_type': 'evm event',
            'asset': 'ETH',
            'address': '0xeB2629a2734e272Bcc07BDA959863f316F4bD4Cf',
            'balance': {'amount': '0.125', 'usd_value': '0'},
            'counterparty': None,
            'event_identifier': '10x6c27ea39e5046646aaf24e1bb451caf466058278685102d89979197fdb89d007',  # noqa: E501
            'event_subtype': 'none',
            'event_type': 'receive',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Receive 0.125 ETH from 0xeB2629a2734e272Bcc07BDA959863f316F4bD4Cf',
            'product': None,
            'sequence_index': 0,
            'timestamp': 1642802651000,
            'tx_hash': '0x6c27ea39e5046646aaf24e1bb451caf466058278685102d89979197fdb89d007',
            'extra_data': None,
        },
    }]
    assert returned_events[4:5] == tx3_events
    tx4_events = [{
        'entry': {
            'identifier': 6,
            'entry_type': 'evm event',
            'asset': A_USDT.identifier,
            'address': '0xE21c192cD270286DBBb0fBa10a8B8D9957d431E5',
            'balance': {'amount': '1166', 'usd_value': '0'},
            'counterparty': None,
            'event_identifier': '10xccb6a445e136492b242d1c2c0221dc4afd4447c96601e88c156ec4d52e993b8f',  # noqa: E501
            'event_subtype': 'none',
            'event_type': 'receive',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Receive 1166 USDT from 0xE21c192cD270286DBBb0fBa10a8B8D9957d431E5 to 0x6e15887E2CEC81434C16D587709f64603b39b545',  # noqa: E501
            'product': None,
            'sequence_index': 385,
            'timestamp': 1642802286000,
            'tx_hash': '0xccb6a445e136492b242d1c2c0221dc4afd4447c96601e88c156ec4d52e993b8f',
            'extra_data': None,
        },
    }]
    assert returned_events[5:6] == tx4_events

    # Now let's edit 1 event and add another one
    event = tx2_events[1]['entry']
    event['asset'] = A_DAI.identifier
    event['balance'] = {'amount': '2500', 'usd_value': '2501.1'}
    event['event_type'] = 'spend'
    event['event_subtype'] = 'payback debt'
    event['notes'] = 'Edited event'
    tx2_events[1]['customized'] = True
    response = requests.patch(
        api_url_for(rotkehlchen_api_server, 'historyeventresource'),
        json={key: value for key, value in event.items() if key not in ('event_identifier',)},
    )
    assert_simple_ok_response(response)

    tx4_events.insert(0, {
        'entry': {
            'entry_type': 'evm event',
            'asset': 'ETH',
            'address': '0xE21c192cD270286DBBb0fBa10a8B8D9957d431E5',
            'balance': {'amount': '1', 'usd_value': '1500.1'},
            'counterparty': CPT_CURVE,
            'event_identifier': '10xccb6a445e136492b242d1c2c0221dc4afd4447c96601e88c156ec4d52e993b8f',  # noqa: E501
            'event_subtype': 'deposit asset',
            'event_type': 'deposit',
            'location': 'ethereum',
            'location_label': '0x6e15887E2CEC81434C16D587709f64603b39b545',
            'notes': 'Some kind of deposit',
            'product': 'pool',
            'sequence_index': 1,
            'timestamp': 1642802286000,
            'tx_hash': '0xccb6a445e136492b242d1c2c0221dc4afd4447c96601e88c156ec4d52e993b8f',
            'extra_data': None,
        },
        'customized': True,
    })
    response = requests.put(
        api_url_for(rotkehlchen_api_server, 'historyeventresource'),
        json={key: value for key, value in tx4_events[0]['entry'].items() if key not in ('event_identifier',)},  # noqa: E501
    )
    result = assert_proper_response_with_result(response)
    tx4_events[0]['entry']['identifier'] = result['identifier']

    # Now let's check DB tables to see they will get modified at purging
    with rotki.data.db.conn.read_ctx() as cursor:
        for name, count in (
                ('evm_transactions', 4), ('evm_internal_transactions', 0),
                ('evmtx_receipts', 4), ('evmtx_receipt_log_topics', 6),
                ('evmtx_address_mappings', 4), ('evm_tx_mappings', 4),
                ('history_events_mappings', 2),
        ):
            assert cursor.execute(f'SELECT COUNT(*) from {name}').fetchone()[0] == count

    # Now purge all transactions of this address and see data is deleted BUT that
    # the edited/added event and all it's tied to is not
    dbevmtx = DBEvmTx(rotki.data.db)
    with rotki.data.db.user_write() as write_cursor:
        dbevmtx.delete_transactions(write_cursor, ethereum_accounts[0], SupportedBlockchain.ETHEREUM)  # noqa: E501

    with rotki.data.db.conn.read_ctx() as cursor:
        for name, count in (
                ('evm_transactions', 2), ('evm_internal_transactions', 0),
                ('evmtx_receipts', 2), ('evmtx_receipt_log_topics', 6),
                ('evmtx_address_mappings', 2), ('evm_tx_mappings', 0),
                ('history_events_mappings', 2),
        ):
            assert cursor.execute(f'SELECT COUNT(*) from {name}').fetchone()[0] == count
        customized_events = dbevents.get_history_events(cursor, EvmEventFilterQuery.make(), True)

    assert customized_events[0].serialize() == tx4_events[0]['entry']
    assert customized_events[1].serialize() == tx2_events[1]['entry']
    # requery all transactions and events. Assert they are the same (different event id though)
    result = query_transactions(rotki)
    entries = result['entries']
    assert len(entries) == 4
    returned_events = query_events(rotkehlchen_api_server, json={'location': 'ethereum'}, expected_num_with_grouping=4, expected_totals_with_grouping=4)  # noqa: E501

    assert len(returned_events) == 7
    assert_serialized_lists_equal(returned_events[0:2], tx1_events, ignore_keys='identifier')
    assert_serialized_lists_equal(returned_events[2:4], tx2_events, ignore_keys='identifier')
    assert_serialized_lists_equal(returned_events[4:5], tx3_events, ignore_keys='identifier')
    assert_serialized_lists_equal(returned_events[5:7], tx4_events, ignore_keys='identifier')

    # explicitly delete the customized (added/edited) transactions
    dbevents.delete_history_events_by_identifier([x.identifier for x in customized_events])

    with rotki.data.db.user_write() as write_cursor:
        # and now purge all transactions again and see everything is deleted
        dbevmtx.delete_transactions(write_cursor, ethereum_accounts[0], SupportedBlockchain.ETHEREUM)  # noqa: E501

    with rotki.data.db.conn.read_ctx() as cursor:
        for name in (
                'evm_transactions', 'evm_internal_transactions',
                'evmtx_receipts', 'evmtx_receipt_log_topics',
                'evmtx_address_mappings', 'evm_tx_mappings',
                'history_events_mappings',
        ):
            assert cursor.execute(f'SELECT COUNT(*) from {name}').fetchone()[0] == 0
        assert dbevents.get_history_events(cursor, EvmEventFilterQuery.make(), True) == []


@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
@patch.object(EthereumTransactions, '_get_transactions_for_range', lambda *args, **kargs: None)
@patch.object(EthereumTransactions, '_get_internal_transactions_for_ranges', lambda *args, **kargs: None)  # noqa: E501
@patch.object(EthereumTransactions, '_get_erc20_transfers_for_ranges', lambda *args, **kargs: None)
@pytest.mark.parametrize('start_with_valid_premium', [True])  # TODO: Test for whichever filters we allow in free  # noqa: E501
def test_events_filter_params(rotkehlchen_api_server, ethereum_accounts, start_with_valid_premium):
    """Tests filtering by transaction's events' properties
    Test cases:
        - Filtering by asset
        - Filtering by protocol (counterparty)
        - Filtering by both asset and a protocol
        - Transaction has multiple related events
        - Transaction has no related events
        - Multiple transactions are queried
        - Filtering by event type
        - Filtering by event subtype
    since the transactions filtered here are created in here and don't come from etherscan
    remove any external query that is not needed
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    db = rotki.data.db
    tx1 = make_ethereum_transaction(tx_hash=b'1', timestamp=1)
    tx2 = make_ethereum_transaction(tx_hash=b'2', timestamp=2)
    tx3 = make_ethereum_transaction(tx_hash=b'3', timestamp=3)
    tx4 = make_ethereum_transaction(tx_hash=b'4', timestamp=4)
    test_contract_address = make_evm_address()
    event1 = make_ethereum_event(tx_hash=b'1', index=1, asset=A_ETH, timestamp=TimestampMS(1), location_label=ethereum_accounts[0], product=EvmProduct.STAKING)  # noqa: E501
    event2 = make_ethereum_event(tx_hash=b'1', index=2, asset=A_ETH, counterparty='EXAMPLE_PROTOCOL', timestamp=TimestampMS(1), location_label=ethereum_accounts[0])  # noqa: E501
    event3 = make_ethereum_event(tx_hash=b'1', index=3, asset=A_WETH, counterparty='EXAMPLE_PROTOCOL', timestamp=TimestampMS(1), location_label=ethereum_accounts[0])  # noqa: E501
    event4 = make_ethereum_event(tx_hash=b'2', index=4, asset=A_WETH, timestamp=TimestampMS(2), location_label=ethereum_accounts[0])  # noqa: E501
    event5 = make_ethereum_event(tx_hash=b'4', index=5, asset=A_DAI, event_type=HistoryEventType.STAKING, event_subtype=HistoryEventSubType.DEPOSIT_ASSET, timestamp=TimestampMS(4), location_label=ethereum_accounts[2], address=test_contract_address)  # noqa: E501
    event6 = make_ethereum_event(tx_hash=b'4', index=6, asset=A_DAI, event_type=HistoryEventType.STAKING, event_subtype=HistoryEventSubType.RECEIVE_WRAPPED, timestamp=TimestampMS(4), location_label=ethereum_accounts[2])  # noqa: E501
    dbevmtx = DBEvmTx(db)
    dbevents = DBHistoryEvents(db)
    with db.user_write() as cursor:
        dbevmtx.add_evm_transactions(cursor, [tx1, tx2], relevant_address=ethereum_accounts[0])
        dbevmtx.add_evm_transactions(cursor, [tx3], relevant_address=ethereum_accounts[1])
        dbevmtx.add_evm_transactions(cursor, [tx4], relevant_address=ethereum_accounts[2])
        dbevents.add_history_events(cursor, [event1, event2, event3, event4, event5, event6])

    for attribute in ('counterparties', 'products'):
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server,
                'historyeventresource',
            ),
            json={
                'location': 'ethereum',
                'asset': A_WETH.serialize(),
                attribute: [],
            },
        )
        assert_error_response(
            response=response,
            contained_in_msg=f'{{"{attribute}": ["List cant be empty"]}}',
        )

    entries_limit = -1 if start_with_valid_premium else FREE_HISTORY_EVENTS_LIMIT
    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'location': 'ethereum',
            'asset': A_WETH.serialize(),
            'location_labels': [ethereum_accounts[0]],
        },
        expected_num_with_grouping=2,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event4, event3])
    assert returned_events == expected

    returned_events = query_events(
        rotkehlchen_api_server,
        json={'asset': A_ETH.serialize(), 'location': 'ethereum'},
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event1, event2])
    assert returned_events == expected

    returned_events = query_events(
        rotkehlchen_api_server,
        json={'asset': A_WETH.serialize(), 'location': 'ethereum'},
        expected_num_with_grouping=2,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event4, event3])
    assert returned_events == expected

    returned_events = query_events(
        rotkehlchen_api_server,
        json={'counterparties': ['EXAMPLE_PROTOCOL'], 'location': 'ethereum'},
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event2, event3])
    assert returned_events == expected

    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'location': 'ethereum',
            'asset': A_WETH.serialize(),
            'counterparties': ['EXAMPLE_PROTOCOL'],
        },
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event3])
    assert returned_events == expected

    # test that filtering by type works
    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'location': 'ethereum',
            'event_types': ['staking'],
        },
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event5, event6])
    assert returned_events == expected

    # test that filtering by subtype works
    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'location': 'ethereum',
            'event_types': ['staking'],
            'event_subtypes': ['deposit_asset'],
        },
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event5])
    assert returned_events == expected

    # test filtering by products
    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'products': [EvmProduct.STAKING.serialize()],
        },
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event1])
    assert returned_events == expected

    # test filtering by address
    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'addresses': [test_contract_address],
        },
        expected_num_with_grouping=1,
        expected_totals_with_grouping=3,
        entries_limit=entries_limit,
    )
    expected = generate_events_response([event5])
    assert returned_events == expected


@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [ONE])
def test_ignored_assets(rotkehlchen_api_server, ethereum_accounts):
    """This test tests that transactions with ignored assets are excluded when needed"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    db = rotki.data.db
    db.add_to_ignored_assets(A_BTC)
    db.add_to_ignored_assets(A_DAI)
    dbevmtx = DBEvmTx(db)
    dbevents = DBHistoryEvents(db)
    tx1 = make_ethereum_transaction(timestamp=1)
    tx2 = make_ethereum_transaction(timestamp=2)
    tx3 = make_ethereum_transaction(timestamp=3)
    event1 = make_ethereum_event(tx_hash=tx1.tx_hash, index=1, asset=A_ETH, timestamp=TimestampMS(1))  # noqa: E501
    event2 = make_ethereum_event(tx_hash=tx1.tx_hash, index=2, asset=A_BTC, timestamp=TimestampMS(1))  # noqa: E501
    event3 = make_ethereum_event(tx_hash=tx1.tx_hash, index=3, asset=A_MKR, timestamp=TimestampMS(1))  # noqa: E501
    event4 = make_ethereum_event(tx_hash=tx2.tx_hash, index=4, asset=A_DAI, timestamp=TimestampMS(2))  # noqa: E501
    with db.user_write() as cursor:
        dbevmtx.add_evm_transactions(cursor, [tx1, tx2, tx3], relevant_address=ethereum_accounts[0])  # noqa: E501
        dbevents.add_history_events(cursor, [event1, event2, event3, event4])

    returned_events = query_events(
        rotkehlchen_api_server,
        json={
            'exclude_ignored_assets': False,
            'location': 'ethereum',
        },
        expected_num_with_grouping=2,
        expected_totals_with_grouping=2,
        entries_limit=100,
    )
    expected = generate_events_response([event4, event1, event2, event3])
    assert returned_events == expected

    returned_events = query_events(
        rotkehlchen_api_server,  # test that default exclude_ignored_assets is True
        json={'location': 'ethereum'},
        expected_num_with_grouping=1,
        expected_totals_with_grouping=2,
        entries_limit=100,
    )
    expected = generate_events_response([event1, event3])
    assert returned_events == expected


@pytest.mark.vcr(filter_query_parameters=['apikey'])
@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('ethereum_accounts', [['0x59ABf3837Fa962d6853b4Cc0a19513AA031fd32b']])
@patch.object(EthereumTransactions, '_get_internal_transactions_for_ranges', lambda *args, **kargs: None)  # noqa: E501
@patch.object(EthereumTransactions, '_get_erc20_transfers_for_ranges', lambda *args, **kargs: None)
@pytest.mark.parametrize('should_mock_price_queries', [True])
@pytest.mark.parametrize('default_mock_price_value', [FVal(1.5)])
def test_no_value_eth_transfer(rotkehlchen_api_server: 'APIServer'):
    """Test that eth transactions with no value are correctly decoded and returned in the API.
    In this case we don't need any erc20 or internal transaction, this is why they are omitted
    in this test.
    """
    tx_str = '0x6cbae2712ded4254cc0dbd3daa9528b049c27095b5216a4c52e2e3be3d6905a5'
    # Make sure that the transactions get decoded
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'evmtransactionsresource',
        ), json={
            'async_query': False,
            'data': [{
                'evm_chain': 'ethereum',
                'tx_hashes': [tx_str],
            }],
        },
    )
    assert_simple_ok_response(response)

    # retrieve the transaction
    response = requests.post(api_url_for(
        rotkehlchen_api_server,
        'evmtransactionsresource',
    ), json={'async_query': False, 'from_timestamp': 1668407732, 'to_timestamp': 1668407737, 'evm_chain': 'ethereum'})  # noqa: E501

    result = assert_proper_response_with_result(response)
    assert len(result['entries']) == 1
    assert result['entries'][0]['entry']['tx_hash'] == tx_str
    # retrieve the event
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'historyeventresource',
        ),
        json={'tx_hashes': [tx_str]},
    )
    result = assert_proper_response_with_result(response)
    assert result['entries'][0]['entry']['asset'] == A_ETH
    assert result['entries'][0]['entry']['balance']['amount'] == '0'


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('ethereum_accounts', [[TEST_ADDR1, TEST_ADDR2]])
def test_decoding_missing_transactions(rotkehlchen_api_server: 'APIServer') -> None:
    """Test that decoding all pending transactions works fine"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    transactions, _ = setup_ethereum_transactions_test(
        database=rotki.data.db,
        transaction_already_queried=True,
        one_receipt_in_db=True,
        second_receipt_in_db=True,
    )
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmpendingtransactionsdecodingresource',
        ), json={'async_query': False, 'data': [{'evm_chain': 'ethereum'}]},
    )
    result = assert_proper_response_with_result(response)
    assert result['decoded_tx_number']['ethereum'] == len(transactions)

    dbevents = DBHistoryEvents(rotki.data.db)
    with rotki.data.db.conn.read_ctx() as cursor:
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=EvmEventFilterQuery.make(
                tx_hashes=[transactions[0].tx_hash],
            ),
            has_premium=True,
        )
        assert len(events) == 3
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=EvmEventFilterQuery.make(
                tx_hashes=[transactions[1].tx_hash],
            ),
            has_premium=True,
        )
        assert len(events) == 2

    # call again and no new transaction should be decoded
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmpendingtransactionsdecodingresource',
        ), json={'async_query': True, 'data': [{'evm_chain': 'ethereum'}]},
    )
    result = assert_proper_response_with_result(response)
    outcome = wait_for_async_task(rotkehlchen_api_server, result['task_id'])
    assert outcome['result']['decoded_tx_number'] == {}


@pytest.mark.parametrize('have_decoders', [True])
@pytest.mark.parametrize('ethereum_accounts', [[TEST_ADDR1, TEST_ADDR2]])
def test_decoding_missing_transactions_by_address(rotkehlchen_api_server: 'APIServer') -> None:
    """Test that decoding all pending transactions works fine when a filter by address is set"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    transactions, _ = extended_transactions_setup_test(
        database=rotki.data.db,
        transaction_already_queried=True,
        one_receipt_in_db=True,
        second_receipt_in_db=True,
    )
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'evmpendingtransactionsdecodingresource',
        ), json={'async_query': False, 'data': [{'evm_chain': 'ethereum', 'addresses': [TEST_ADDR1, TEST_ADDR3]}]},  # noqa: E501
    )
    result = assert_proper_response_with_result(response)

    transactions_filtered = []
    for transaction in transactions:
        tx_addreses = (transaction.from_address, transaction.to_address)
        if TEST_ADDR1 in tx_addreses or TEST_ADDR3 in tx_addreses:
            transactions_filtered.append(transaction)

    assert result['decoded_tx_number']['ethereum'] == len(transactions_filtered)

    dbevents = DBHistoryEvents(rotki.data.db)
    with rotki.data.db.conn.read_ctx() as cursor:
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=EvmEventFilterQuery.make(
                tx_hashes=[transactions[0].tx_hash],
            ),
            has_premium=True,
        )
        assert len(events) == 3
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=EvmEventFilterQuery.make(
                tx_hashes=[transactions[1].tx_hash],
            ),
            has_premium=True,
        )
        assert len(events) == 0
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=EvmEventFilterQuery.make(
                tx_hashes=[transactions[2].tx_hash],
            ),
            has_premium=True,
        )
        assert len(events) == 2
