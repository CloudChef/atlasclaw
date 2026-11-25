/*
 *  Copyright 2026  Qianyun, Inc., All rights reserved.
 */

/**
 * chat-attachments.js unit tests: document support and error contracts.
 */

global.fetch = jest.fn();

beforeEach(() => {
    jest.resetModules();
    global.fetch.mockClear();
});

describe('chat-attachments.js', () => {
    test('accepts images and documents in the shared format list', async () => {
        const { ACCEPTED_FORMATS, isImageFile } = await import('../../app/frontend/scripts/chat-attachments.js');
        for (const extension of ['.png', '.pdf', '.docx', '.txt', '.csv']) {
            expect(ACCEPTED_FORMATS).toContain(extension);
        }
        expect(isImageFile({ name: 'photo.PNG' })).toBe(true);
        expect(isImageFile({ name: 'report.pdf', type: 'application/pdf' })).toBe(false);
    });

    test('rejects too many files and oversized documents before uploading', async () => {
        const { uploadChatAttachments } = await import('../../app/frontend/scripts/chat-attachments.js');
        await expect(uploadChatAttachments('session', new Array(5).fill({ name: 'a.txt', size: 1 })))
            .rejects.toEqual({ code: 'too_many_files' });
        await expect(uploadChatAttachments('session', [{ name: 'big.pdf', size: 21 * 1024 * 1024 }]))
            .rejects.toEqual({ code: 'file_too_large' });
        await expect(uploadChatAttachments('session', [{ name: 'big.png', size: 6 * 1024 * 1024 }]))
            .rejects.toEqual({ code: 'image_too_large' });
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('maps history documents to file attachments with inline preview URLs', async () => {
        const { historyAttachmentFiles } = await import('../../app/frontend/scripts/chat-attachments.js');
        const files = historyAttachmentFiles([
            { id: 'doc-1', name: 'report.pdf', kind: 'document', available: true },
            { id: 'img-1', name: 'photo.png', kind: 'image', available: true },
            { id: 'gone', name: 'old.pdf', kind: 'document', available: false }
        ]);
        expect(files).toHaveLength(2);
        expect(files[0]).toEqual({
            name: 'report.pdf', type: 'file',
            src: '/api/chat/attachments/doc-1/content?inline=1'
        });
        expect(files[1].type).toBe('image');
        expect(files[1].src).not.toContain('inline=1');
    });

    test('falls back to the server message for unknown error codes', async () => {
        const { attachmentErrorMessage } = await import('../../app/frontend/scripts/chat-attachments.js');
        expect(attachmentErrorMessage({ code: 'something_new', message: 'Boom' })).toBe('Boom');
        expect(attachmentErrorMessage('network down')).toBe('network down');
    });
});
