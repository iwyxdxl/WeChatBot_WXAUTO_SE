/*
 Navicat Premium Dump SQL

 Source Server         : 起元
 Source Server Type    : MySQL
 Source Server Version : 50744 (5.7.44-log)
 Source Host           : 43.139.211.77:3306
 Source Schema         : wechat_bot

 Target Server Type    : MySQL
 Target Server Version : 50744 (5.7.44-log)
 File Encoding         : 65001

 Date: 02/07/2025 11:03:38
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for group_chat_messages
-- ----------------------------
DROP TABLE IF EXISTS `group_chat_messages`;
CREATE TABLE `group_chat_messages` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `group_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '群聊ID',
  `speaker` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '发言者',
  `message_content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '消息内容',
  `message_time` datetime NOT NULL COMMENT '消息时间',
  `prompt_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色名称',
  `message_type` enum('user','ai','system') COLLATE utf8mb4_unicode_ci DEFAULT 'user' COMMENT '消息类型',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_group_time` (`group_id`,`message_time`),
  KEY `idx_message_time` (`message_time`),
  KEY `idx_group_speaker_time` (`group_id`,`speaker`,`message_time`)
) ENGINE=InnoDB AUTO_INCREMENT=25 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='群聊消息记录表';

-- ----------------------------
-- Table structure for group_summaries
-- ----------------------------
DROP TABLE IF EXISTS `group_summaries`;
CREATE TABLE `group_summaries` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `group_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '群聊名称',
  `summary_content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '总结内容',
  `summary_date` date NOT NULL COMMENT '总结日期',
  `time_range` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '时间范围',
  `message_count` int(11) DEFAULT '0' COMMENT '消息数量',
  `prompt_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色名称',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_summary_date` (`summary_date`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='群聊总结记录表';

-- ----------------------------
-- Table structure for user_chat_messages
-- ----------------------------
DROP TABLE IF EXISTS `user_chat_messages`;
CREATE TABLE `user_chat_messages` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '用户ID',
  `speaker` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '发言者',
  `message_content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '消息内容',
  `message_time` datetime NOT NULL COMMENT '消息时间',
  `prompt_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色名称',
  `message_type` enum('user','ai','system') COLLATE utf8mb4_unicode_ci DEFAULT 'user' COMMENT '消息类型',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_user_time` (`user_id`,`message_time`),
  KEY `idx_message_time` (`message_time`),
  KEY `idx_user_speaker_time` (`user_id`,`speaker`,`message_time`)
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户聊天消息记录表';

SET FOREIGN_KEY_CHECKS = 1;
