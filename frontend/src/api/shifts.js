import {api} from './client.js';
export const shiftsApi={
  list:()=>api('/shifts'),
  current:()=>api('/shifts/current'),
  sign:(shift_id,next_code)=>api('/shifts/sign','POST',{shift_id,next_code}),
  backfill:id=>api('/shifts/'+id+'/backfill','POST')};
